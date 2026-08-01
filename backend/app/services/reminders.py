"""Påminnelser: automatgenerering från anläggningsdata, samt utskick när de närmar sig."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Customer, Facility, Quote, Reminder, Visit, WorkOrder
from .notify import DEFAULT_SMTP, SMTP_KEY, get_setting, send_email, send_push

# Hur långt före förfallodatum en påminnelse ska meddelas
LEAD_DAYS = {
    "service": 30,
    "vattenprov": 30,
    "intyg": 45,
    "uppfoljning": 0,
    "egen": 0,
    # Affärspåminnelser: de förfaller på dagen, ingen förvarning behövs
    "betalning": 0,
    "offert": 0,
    "besok": 0,
}


def add_months(iso: str, months: int) -> str | None:
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day).isoformat()


async def generate_auto(db: AsyncSession) -> int:
    """Skapar de automatiska påminnelserna. Idempotent tack vare auto_key."""
    facilities = (await db.execute(select(Facility).join(Customer))).unique().scalars().all()
    existing = set(
        (await db.execute(select(Reminder.auto_key).where(Reminder.auto_key.isnot(None))))
        .scalars()
        .all()
    )
    created = 0

    for f in facilities:
        name = f.customer.name if f.customer else "kund"
        plans = []

        if f.last_service_at and f.service_interval_months:
            due = add_months(f.last_service_at, f.service_interval_months)
            if due:
                plans.append(
                    ("service", due, f"Service {f.facility_no}",
                     f"{name}: {f.facility_type.lower()} ska servas. "
                     f"Senaste service {f.last_service_at}, intervall {f.service_interval_months} mån.")
                )

        if f.water_sample_at and f.water_sample_valid_months:
            due = add_months(f.water_sample_at, f.water_sample_valid_months)
            if due:
                plans.append(
                    ("vattenprov", due, f"Vattenprov {f.facility_no}",
                     f"{name}: senaste provet togs {f.water_sample_at}. Dags för nytt prov.")
                )

        if f.certificate_expires_at:
            label = f.certificate_label or "Intyg"
            plans.append(
                ("intyg", f.certificate_expires_at, f"{label} går ut, {f.facility_no}",
                 f"{name}: {label.lower()} går ut {f.certificate_expires_at}.")
            )

        for kind, due, title, body in plans:
            key = f"{kind}:{f.id}:{due}"
            if key in existing:
                continue
            db.add(
                Reminder(
                    customer_id=f.customer_id,
                    facility_id=f.id,
                    kind=kind,
                    title=title,
                    body=body,
                    due_date=due,
                    notify_days_before=LEAD_DAYS.get(kind, 14),
                    remind_at=datetime.combine(
                        date.fromisoformat(due) - timedelta(days=LEAD_DAYS.get(kind, 14)),
                        time(5, 0),
                        tzinfo=timezone.utc,
                    ),
                    auto_key=key,
                    created_by="system",
                )
            )
            existing.add(key)
            created += 1

    if created:
        await db.commit()
    return created


def berakna_remind_at(r: Reminder) -> datetime | None:
    """Tidpunkt att meddela. Är den inte satt räknas den ut ur förfallodag och förvarning."""
    if r.remind_at is not None:
        return r.remind_at if r.remind_at.tzinfo else r.remind_at.replace(tzinfo=timezone.utc)
    try:
        forfaller = date.fromisoformat(r.due_date)
    except (ValueError, TypeError):
        return None
    dag = forfaller - timedelta(days=r.notify_days_before or 0)
    # Klockan 07:00 svensk tid, alltså 05:00 UTC vintertid. Räcker för en morgonrutin.
    return datetime.combine(dag, time(5, 0), tzinfo=timezone.utc)


async def backfill_remind_at(db: AsyncSession) -> int:
    """Fyller i tidpunkt på rader som skapades innan fältet fanns."""
    rows = (
        await db.execute(
            select(Reminder).where(Reminder.remind_at.is_(None), Reminder.status == "open")
        )
    ).scalars().all()
    antal = 0
    for r in rows:
        tid = berakna_remind_at(r)
        if tid:
            r.remind_at = tid
            antal += 1
    if antal:
        await db.commit()
    return antal


async def generate_business(db: AsyncSession) -> int:
    """Påminnelser om pengar och återkoppling.

    Tre saker som annars rinner ut i sanden: en faktura som inte betalats, en
    offert som ingen svarat på, och ett besök som passerat utan att något hänt.
    Idempotent tack vare auto_key, precis som serviceintervallen.
    """
    from .notify import get_setting

    conf = await get_setting(db, "foretag", {})
    betalningsvillkor = int(conf.get("betalningsvillkor_dagar") or 30)
    obetald_efter = int(conf.get("paminn_obetald_efter_dagar") or 7)
    offert_efter = int(conf.get("paminn_offert_efter_dagar") or 10)

    befintliga = set(
        (await db.execute(select(Reminder.auto_key).where(Reminder.auto_key.isnot(None))))
        .scalars()
        .all()
    )
    idag = date.today()
    skapade = 0

    def lagg(nyckel, kind, titel, text, forfaller, customer_id=None, facility_id=None):
        nonlocal skapade
        if nyckel in befintliga:
            return
        db.add(
            Reminder(
                customer_id=customer_id,
                facility_id=facility_id,
                kind=kind,
                title=titel,
                body=text,
                due_date=forfaller,
                notify_days_before=0,
                remind_at=datetime.combine(
                    date.fromisoformat(forfaller), time(5, 0), tzinfo=timezone.utc
                ),
                auto_key=nyckel,
                created_by="system",
            )
        )
        befintliga.add(nyckel)
        skapade += 1

    kundnamn = dict(
        (await db.execute(select(Customer.id, Customer.name))).all()
    )

    # 1. Fakturerat men inte betalt
    order = (
        await db.execute(select(WorkOrder).where(WorkOrder.status == "fakturerad"))
    ).scalars().all()
    for o in order:
        if not o.invoiced_at:
            continue
        try:
            fakturerad = date.fromisoformat(o.invoiced_at)
        except ValueError:
            continue
        forfallodag = fakturerad + timedelta(days=betalningsvillkor)
        paminn = forfallodag + timedelta(days=obetald_efter)
        if paminn > idag:
            continue
        namn = kundnamn.get(o.customer_id, "kunden")
        lagg(
            f"betalning:{o.id}:{o.invoiced_at}",
            "betalning",
            f"Obetald faktura: {o.order_no}",
            f"{namn}: fakturan skickades {o.invoiced_at}"
            + (f" med nummer {o.invoice_no}" if o.invoice_no else "")
            + f" och förföll {forfallodag.isoformat()}. Ingen betalning registrerad.",
            paminn.isoformat(),
            customer_id=o.customer_id,
            facility_id=o.facility_id,
        )

    # 2. Offert skickad utan besked
    offerter = (
        await db.execute(select(Quote).where(Quote.status == "skickad"))
    ).scalars().all()
    for q in offerter:
        if q.sent_at is None:
            continue
        skickad = q.sent_at.date() if hasattr(q.sent_at, "date") else None
        if skickad is None:
            continue
        paminn = skickad + timedelta(days=offert_efter)
        if paminn > idag:
            continue
        namn = kundnamn.get(q.customer_id, q.recipient_name or "mottagaren")
        lagg(
            f"offert:{q.id}:{skickad.isoformat()}",
            "offert",
            f"Följ upp offert {q.quote_no}",
            f"{namn}: offerten skickades {skickad.isoformat()} och har inte fått besked."
            + (f" Gäller till {q.valid_until}." if q.valid_until else ""),
            paminn.isoformat(),
            customer_id=q.customer_id,
            facility_id=q.facility_id,
        )

    # 3. Besök som passerat utan att något hänt
    besok = (
        await db.execute(select(Visit).where(Visit.status.in_(["planerat", "genomfort"])))
    ).scalars().all()
    for v in besok:
        if not v.planned_at:
            continue
        try:
            planerat = date.fromisoformat(v.planned_at)
        except ValueError:
            continue
        paminn = planerat + timedelta(days=3)
        if paminn > idag:
            continue
        lagg(
            f"besok:{v.id}:{v.planned_at}",
            "besok",
            f"Återkoppla till {v.contact_name or v.visit_no}",
            f"Besöket var inbokat {v.planned_at} och står fortfarande som "
            f"{'inbokat' if v.status == 'planerat' else 'besökt utan offert'}."
            + (f" Ärende: {v.errand}" if v.errand else ""),
            paminn.isoformat(),
            customer_id=v.customer_id,
        )

    if skapade:
        await db.commit()
    return skapade


async def stang_inaktuella(db: AsyncSession) -> int:
    """Kvitterar automatiska påminnelser vars anledning försvunnit.

    Betalas fakturan eller kommer besked på offerten ska påminnelsen inte ligga
    kvar och skava. Egna påminnelser rörs aldrig.
    """
    oppna = (
        await db.execute(
            select(Reminder).where(
                Reminder.status == "open",
                Reminder.kind.in_(["betalning", "offert", "besok"]),
                Reminder.auto_key.isnot(None),
            )
        )
    ).scalars().all()
    stangda = 0
    for r in oppna:
        typ, _, resten = (r.auto_key or "").partition(":")
        objekt_id = resten.split(":")[0]
        klar = False
        if typ == "betalning":
            o = (
                await db.execute(select(WorkOrder).where(WorkOrder.id == objekt_id))
            ).scalar_one_or_none()
            klar = o is None or o.status in ("betald", "makulerad")
        elif typ == "offert":
            q = (
                await db.execute(select(Quote).where(Quote.id == objekt_id))
            ).scalar_one_or_none()
            klar = q is None or q.status != "skickad"
        elif typ == "besok":
            v = (
                await db.execute(select(Visit).where(Visit.id == objekt_id))
            ).scalar_one_or_none()
            klar = v is None or v.status in ("offert", "vunnen", "forlorad")
        if klar:
            r.status = "done"
            r.completed_at = datetime.now(timezone.utc)
            r.completed_by = "system"
            stangda += 1
    if stangda:
        await db.commit()
    return stangda


async def due_now(db: AsyncSession) -> list[Reminder]:
    """Öppna påminnelser vars tidpunkt passerats och som ännu inte meddelats."""
    nu = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Reminder).where(Reminder.status == "open", Reminder.notified_at.is_(None))
        )
    ).scalars().all()
    klara = []
    for r in rows:
        tid = berakna_remind_at(r)
        if tid is not None and tid <= nu:
            klara.append(r)
    return sorted(klara, key=lambda r: (r.due_date, r.due_time))


async def notify_due(db: AsyncSession, force: bool = False) -> dict:
    """Skickar en samlad påminnelse per körning, inte ett mejl per rad."""
    items = await due_now(db)
    if not items:
        return {"reminders": 0, "email": False, "push": 0}

    lines = []
    for r in items:
        customer = ""
        if r.customer_id:
            c = (
                await db.execute(select(Customer.name).where(Customer.id == r.customer_id))
            ).scalar_one_or_none()
            customer = f" ({c})" if c else ""
        nar = f"{r.due_date} {r.due_time}".strip()
        lines.append(f"- {nar}  {r.title}{customer}")

    subject = (
        f"Borrjournal: {len(items)} påminnelse"
        f"{'r' if len(items) > 1 else ''} att hantera"
    )
    body = (
        "Följande påminnelser är inom sitt förvarningsfönster:\n\n"
        + "\n".join(lines)
        + "\n\nÖppna Borrjournal för att kvittera eller boka in dem.\n"
    )

    email_sent = False
    smtp = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    if smtp.get("enabled"):
        try:
            await send_email(smtp, subject, body)
            email_sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[borrjournal] e-postutskick misslyckades: {exc}")

    pushed = await send_push(
        db,
        {
            "title": subject,
            "body": lines[0].lstrip("- ") + (f" och {len(items) - 1} till" if len(items) > 1 else ""),
            "url": "/#/paminnelser",
            "tag": "paminnelser",
        },
    )

    stamp = datetime.now(timezone.utc)
    channels = ",".join([c for c, on in (("epost", email_sent), ("push", pushed > 0)) if on])
    for r in items:
        r.notified_at = stamp
        r.notified_channels = channels
    await db.commit()

    return {"reminders": len(items), "email": email_sent, "push": pushed}
