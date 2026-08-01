from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import iso_utc
from ..models import Customer, Facility, Reminder, User
from ..security import current_user, log_action, require_write
from ..services import reminders as svc

router = APIRouter(prefix="/api/reminders", tags=["paminnelser"])

KINDS = {"service", "vattenprov", "intyg", "uppfoljning", "egen", "betalning", "offert", "besok"}


class ReminderIn(BaseModel):
    title: str
    due_date: str
    due_time: str = ""
    # ISO-tidpunkt med tidszon, t.ex. 2026-08-14T07:30:00+02:00. Klienten räknar
    # om från användarens lokala tid så att 07:30 betyder 07:30 där hen står.
    remind_at: str | None = None
    body: str = ""
    kind: str = "egen"
    customer_id: str | None = None
    facility_id: str | None = None
    journal_id: str | None = None
    notify_days_before: int = 7
    assigned_to: str | None = None


def _tolka_tid(varde: str | None) -> datetime | None:
    if not varde:
        return None
    try:
        d = datetime.fromisoformat(varde.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Ogiltig tidpunkt, ange den som ISO-datum med tidszon"
        ) from None
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def out(r: Reminder, customer_name: str = "") -> dict:
    today = date.today().isoformat()
    overdue = r.status == "open" and r.due_date < today
    return {
        "id": r.id,
        "kind": r.kind,
        "title": r.title,
        "body": r.body,
        "due_date": r.due_date,
        "due_time": r.due_time,
        "remind_at": iso_utc(r.remind_at) if r.remind_at else None,
        "days_left": (date.fromisoformat(r.due_date) - date.today()).days
        if _valid(r.due_date)
        else None,
        "overdue": overdue,
        "status": r.status,
        "customer_id": r.customer_id,
        "customer_name": customer_name,
        "facility_id": r.facility_id,
        "journal_id": r.journal_id,
        "notify_days_before": r.notify_days_before,
        "notified_at": iso_utc(r.notified_at) if r.notified_at else None,
        "notified_channels": r.notified_channels,
        "auto": bool(r.auto_key),
        "assigned_to": r.assigned_to,
        "assigned_name": "",
        "created_by": r.created_by,
        "completed_at": iso_utc(r.completed_at) if r.completed_at else None,
        "completed_by": r.completed_by,
    }


def _valid(iso: str) -> bool:
    try:
        date.fromisoformat(iso)
        return True
    except (ValueError, TypeError):
        return False


async def names(db: AsyncSession, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (
        await db.execute(select(Customer.id, Customer.name).where(Customer.id.in_(ids)))
    ).all()
    return {r[0]: r[1] for r in rows}


@router.get("")
async def list_reminders(
    status: str = "open",
    customer_id: str | None = None,
    kind: str | None = None,
    scope: str = "alla",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Reminder).order_by(Reminder.due_date)
    if scope == "mina":
        # Mina egna, plus sådana som ingen tagit ansvar för
        stmt = stmt.where(
            or_(Reminder.assigned_to == user.id, Reminder.assigned_to.is_(None))
        )
    if status in ("open", "done"):
        stmt = stmt.where(Reminder.status == status)
    if customer_id:
        stmt = stmt.where(Reminder.customer_id == customer_id)
    if kind:
        stmt = stmt.where(Reminder.kind == kind)
    rows = (await db.execute(stmt)).scalars().all()
    lookup = await names(db, {r.customer_id for r in rows})

    from ..models import User as _User

    agare = dict(
        (
            await db.execute(
                select(_User.id, _User.full_name).where(
                    _User.id.in_({r.assigned_to for r in rows if r.assigned_to})
                )
            )
        ).all()
    ) if any(r.assigned_to for r in rows) else {}

    ut = []
    for r in rows:
        d = out(r, lookup.get(r.customer_id, ""))
        d["assigned_name"] = agare.get(r.assigned_to, "")
        d["mine"] = r.assigned_to == user.id
        ut.append(d)
    return ut


@router.get("/summary")
async def summary(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Reminder).where(Reminder.status == "open"))
    ).scalars().all()
    mina = [r for r in rows if r.assigned_to == user.id or r.assigned_to is None]
    today = date.today().isoformat()
    week = (date.today() + timedelta(days=7)).isoformat()
    overdue = [r for r in rows if _valid(r.due_date) and r.due_date < today]
    soon = [
        r
        for r in rows
        if _valid(r.due_date)
        and today <= r.due_date
        and (date.fromisoformat(r.due_date) - date.today()).days <= 30
    ]
    this_week = [r for r in rows if _valid(r.due_date) and r.due_date <= week]
    return {
        "open": len(rows),
        "overdue": len(overdue),
        "this_week": len(this_week),
        "next_30_days": len(soon),
        "today": today,
        "mina_open": len(mina),
        "mina_overdue": len([r for r in mina if _valid(r.due_date) and r.due_date < today]),
        "notify_scope": user.notify_scope,
    }


@router.post("", status_code=201)
async def create_reminder(
    payload: ReminderIn,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    if not _valid(payload.due_date):
        raise HTTPException(status_code=400, detail="Ange ett datum i formatet ÅÅÅÅ-MM-DD")
    if payload.kind not in KINDS:
        raise HTTPException(status_code=400, detail="Okänd typ av påminnelse")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Påminnelsen behöver en rubrik")

    # Egna påminnelser måste höra till en anläggning. Automatgenererade har alltid en.
    if payload.kind == "egen" and not payload.facility_id:
        raise HTTPException(
            status_code=400, detail="Välj vilken anläggning påminnelsen gäller"
        )

    # Kunden fylls i automatiskt från anläggningen, så inget hamnar löst i luften
    if payload.facility_id:
        f = (
            await db.execute(select(Facility).where(Facility.id == payload.facility_id))
        ).unique().scalar_one_or_none()
        if f is None:
            raise HTTPException(status_code=404, detail="Anläggningen finns inte")
        payload.customer_id = f.customer_id

    data = payload.model_dump()
    tid = _tolka_tid(data.pop("remind_at", None))
    if not data.get("assigned_to"):
        data["assigned_to"] = user.id
    r = Reminder(**data, created_by=user.full_name or user.username)
    r.remind_at = tid or svc.berakna_remind_at(r)
    r.title = r.title.strip()
    db.add(r)
    await db.commit()
    await db.refresh(r)
    await log_action(
        db,
        "REMINDER_CREATE",
        actor=user.username,
        object_type="reminder",
        object_id=r.id,
        request=request,
        detail=f"{r.due_date} {r.title}",
    )
    lookup = await names(db, {r.customer_id})
    return out(r, lookup.get(r.customer_id, ""))


@router.patch("/{reminder_id}")
async def update_reminder(
    reminder_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    r = (
        await db.execute(select(Reminder).where(Reminder.id == reminder_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Påminnelsen finns inte")

    if "done" in payload:
        if payload["done"]:
            r.status = "done"
            r.completed_at = datetime.now(timezone.utc)
            r.completed_by = user.full_name or user.username
        else:
            r.status = "open"
            r.completed_at = None
            r.completed_by = ""
    if payload.get("snooze_days"):
        if not _valid(r.due_date):
            raise HTTPException(status_code=400, detail="Påminnelsen har inget giltigt datum")
        dagar = int(payload["snooze_days"])
        moved = date.fromisoformat(r.due_date) + timedelta(days=dagar)
        r.due_date = moved.isoformat()
        if r.remind_at:
            r.remind_at = r.remind_at + timedelta(days=dagar)
        r.notified_at = None
    for field in ("title", "body", "due_date", "due_time", "notify_days_before", "assigned_to"):
        if field in payload:
            setattr(r, field, payload[field])
    if "remind_at" in payload:
        r.remind_at = _tolka_tid(payload["remind_at"])
        r.notified_at = None
    for field in ("title", "body", "due_date", "due_time", "notify_days_before"):
        if field in payload and "remind_at" not in payload:
            r.remind_at = svc.berakna_remind_at(
                Reminder(
                    due_date=r.due_date,
                    notify_days_before=r.notify_days_before,
                    remind_at=None,
                )
            )
            break
    if "due_date" in payload:
        if not _valid(r.due_date):
            raise HTTPException(status_code=400, detail="Ange ett datum i formatet ÅÅÅÅ-MM-DD")
        r.notified_at = None

    await db.commit()
    await db.refresh(r)
    await log_action(
        db,
        "REMINDER_UPDATE",
        actor=user.username,
        object_type="reminder",
        object_id=r.id,
        request=request,
        detail=r.status,
    )
    lookup = await names(db, {r.customer_id})
    return out(r, lookup.get(r.customer_id, ""))


@router.delete("/{reminder_id}", status_code=204)
async def delete_reminder(
    reminder_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    r = (
        await db.execute(select(Reminder).where(Reminder.id == reminder_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Påminnelsen finns inte")
    await db.delete(r)
    await db.commit()
    await log_action(
        db,
        "REMINDER_DELETE",
        actor=user.username,
        object_type="reminder",
        object_id=reminder_id,
        request=request,
    )


@router.post("/scan")
async def scan(
    notify: bool = False,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Kör automatgenereringen direkt, t.ex. efter att ett serviceintervall ändrats."""
    created = await svc.generate_auto(db)
    created += await svc.generate_business(db)
    stangda = await svc.stang_inaktuella(db)
    result = {"created": created, "closed": stangda}
    if notify:
        result.update(await svc.notify_due(db))
    return result
