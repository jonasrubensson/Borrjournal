from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, Facility, Reminder, User
from ..security import current_user, log_action, require_write
from ..services import reminders as svc

router = APIRouter(prefix="/api/reminders", tags=["paminnelser"])

KINDS = {"service", "vattenprov", "intyg", "uppfoljning", "egen"}


class ReminderIn(BaseModel):
    title: str
    due_date: str
    body: str = ""
    kind: str = "egen"
    customer_id: str | None = None
    facility_id: str | None = None
    journal_id: str | None = None
    notify_days_before: int = 7
    assigned_to: str | None = None


def out(r: Reminder, customer_name: str = "") -> dict:
    today = date.today().isoformat()
    overdue = r.status == "open" and r.due_date < today
    return {
        "id": r.id,
        "kind": r.kind,
        "title": r.title,
        "body": r.body,
        "due_date": r.due_date,
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
        "notified_at": r.notified_at.isoformat() if r.notified_at else None,
        "notified_channels": r.notified_channels,
        "auto": bool(r.auto_key),
        "created_by": r.created_by,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
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
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Reminder).order_by(Reminder.due_date)
    if status in ("open", "done"):
        stmt = stmt.where(Reminder.status == status)
    if customer_id:
        stmt = stmt.where(Reminder.customer_id == customer_id)
    if kind:
        stmt = stmt.where(Reminder.kind == kind)
    rows = (await db.execute(stmt)).scalars().all()
    lookup = await names(db, {r.customer_id for r in rows})
    return [out(r, lookup.get(r.customer_id, "")) for r in rows]


@router.get("/summary")
async def summary(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Reminder).where(Reminder.status == "open"))
    ).scalars().all()
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

    # Väljs en anläggning ska kunden fyllas i automatiskt, så inget hamnar löst i luften
    if payload.facility_id:
        f = (
            await db.execute(select(Facility).where(Facility.id == payload.facility_id))
        ).unique().scalar_one_or_none()
        if f is None:
            raise HTTPException(status_code=404, detail="Anläggningen finns inte")
        payload.customer_id = f.customer_id

    r = Reminder(
        **payload.model_dump(),
        created_by=user.full_name or user.username,
    )
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
        moved = date.fromisoformat(r.due_date) + timedelta(days=int(payload["snooze_days"]))
        r.due_date = moved.isoformat()
        r.notified_at = None
    for field in ("title", "body", "due_date", "notify_days_before", "assigned_to"):
        if field in payload:
            setattr(r, field, payload[field])
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
    result = {"created": created}
    if notify:
        result.update(await svc.notify_due(db))
    return result
