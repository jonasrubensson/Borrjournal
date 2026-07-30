"""Påminnelser: automatgenerering från anläggningsdata, samt utskick när de närmar sig."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Customer, Facility, Reminder
from .notify import DEFAULT_SMTP, SMTP_KEY, get_setting, send_email, send_push

# Hur långt före förfallodatum en påminnelse ska meddelas
LEAD_DAYS = {"service": 30, "vattenprov": 30, "intyg": 45, "uppfoljning": 0, "egen": 0}


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
                    auto_key=key,
                    created_by="system",
                )
            )
            existing.add(key)
            created += 1

    if created:
        await db.commit()
    return created


async def due_now(db: AsyncSession) -> list[Reminder]:
    """Öppna påminnelser inom sitt förvarningsfönster som ännu inte meddelats."""
    today = date.today()
    rows = (
        await db.execute(
            select(Reminder).where(Reminder.status == "open", Reminder.notified_at.is_(None))
        )
    ).scalars().all()
    ready = []
    for r in rows:
        try:
            due = date.fromisoformat(r.due_date)
        except (ValueError, TypeError):
            continue
        if due - timedelta(days=r.notify_days_before or 0) <= today:
            ready.append(r)
    return sorted(ready, key=lambda r: r.due_date)


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
        lines.append(f"- {r.due_date}  {r.title}{customer}")

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
