from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, Facility, JournalEntry, User
from ..schemas import (
    CustomerIn,
    FacilityIn,
    NewFacilityIn,
    customer_out,
    facility_out,
)
from ..security import current_user, log_action, require_admin, require_write
from ..services.geo import parse_coordinates
from ..services.geocode import geocode_safe
from ..services.reminders import generate_auto

router = APIRouter(prefix="/api", tags=["kunder"])


async def next_no(db: AsyncSession, model, column, prefix: str, start: int) -> str:
    count = (await db.execute(select(func.count()).select_from(model))).scalar() or 0
    candidate = start + count + 1
    while True:
        no = f"{prefix}-{candidate}"
        taken = (await db.execute(select(model.id).where(column == no))).first()
        if not taken:
            return no
        candidate += 1


def apply_coordinates(data: dict) -> dict:
    """Fyller lat/lon från fritextfältet om montören inte angett dem direkt.
    Klarar decimalgrader, SWEREF 99 TM och grader med minuter."""
    if data.get("latitude") is None or data.get("longitude") is None:
        parsed = parse_coordinates(data.get("coordinates", "") or "")
        if parsed:
            data["latitude"], data["longitude"] = parsed
    return data


async def auto_koordinat_anlaggning(facility: Facility, customer: Customer) -> None:
    """Fyller anläggningens koordinat från kundens adress om den saknas."""
    if facility.latitude is not None and facility.longitude is not None:
        return
    adress = ", ".join(
        x for x in (customer.address, customer.property_designation) if x
    )
    if not adress:
        return
    hit = await geocode_safe(adress, customer.municipality or "")
    if hit:
        facility.latitude = hit["latitude"]
        facility.longitude = hit["longitude"]
        if not facility.coordinates:
            facility.coordinates = f"{hit['latitude']}, {hit['longitude']}"


async def get_customer(db: AsyncSession, customer_id: str) -> Customer:
    c = (await db.execute(select(Customer).where(Customer.id == customer_id))).unique().scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Kunden finns inte")
    return c


@router.get("/customers")
async def list_customers(
    q: str | None = None,
    municipality: str | None = None,
    status: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Customer).order_by(Customer.name)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Customer.name).like(needle),
                func.lower(Customer.customer_no).like(needle),
                func.lower(Customer.property_designation).like(needle),
                func.lower(Customer.municipality).like(needle),
                func.lower(Customer.address).like(needle),
                func.lower(Customer.phone).like(needle),
                func.lower(Customer.email).like(needle),
                func.lower(Customer.org_no).like(needle),
            )
        )
    if municipality:
        stmt = stmt.where(func.lower(Customer.municipality) == municipality.lower())

    rows = (await db.execute(stmt)).unique().scalars().all()
    out = [customer_out(c) for c in rows]
    if status:
        out = [c for c in out if c["status"] == status]
    return out


@router.post("/customers", status_code=201)
async def create_customer(
    payload: CustomerIn,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    c = Customer(
        customer_no=await next_no(db, Customer, Customer.customer_no, "K", 1000),
        **payload.model_dump(),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    await log_action(
        db, "CUSTOMER_CREATE", actor=user.username, object_type="customer", object_id=c.id, request=request
    )
    return customer_out(c)


@router.get("/customers/{customer_id}")
async def read_customer(
    customer_id: str, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    return customer_out(await get_customer(db, customer_id))


@router.patch("/customers/{customer_id}")
async def update_customer(
    customer_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    c = await get_customer(db, customer_id)
    allowed = set(CustomerIn.model_fields.keys())
    for key, value in payload.items():
        if key in allowed:
            setattr(c, key, value)
    await db.commit()
    await db.refresh(c)
    await log_action(
        db, "CUSTOMER_UPDATE", actor=user.username, object_type="customer", object_id=c.id, request=request
    )
    return customer_out(c)


# ---------- anläggningar ----------
@router.post("/customers/{customer_id}/facilities", status_code=201)
async def create_facility(
    customer_id: str,
    payload: FacilityIn,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    await get_customer(db, customer_id)
    f = Facility(
        facility_no=await next_no(db, Facility, Facility.facility_no, "B", 2000),
        customer_id=customer_id,
        **apply_coordinates(payload.model_dump()),
    )
    db.add(f)
    await db.flush()
    await auto_koordinat_anlaggning(f, await get_customer(db, customer_id))
    await db.commit()
    await db.refresh(f)
    await generate_auto(db)
    await log_action(
        db, "FACILITY_CREATE", actor=user.username, object_type="facility", object_id=f.id, request=request
    )
    return facility_out(f)


@router.patch("/facilities/{facility_id}")
async def update_facility(
    facility_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    f = (
        await db.execute(select(Facility).where(Facility.id == facility_id))
    ).unique().scalar_one_or_none()
    if f is None:
        raise HTTPException(status_code=404, detail="Anläggningen finns inte")
    allowed = set(FacilityIn.model_fields.keys())
    if "coordinates" in payload and "latitude" not in payload:
        payload = apply_coordinates({**payload, "latitude": None, "longitude": None})
    for key, value in payload.items():
        if key in allowed:
            setattr(f, key, value)
    await db.commit()
    await db.refresh(f)
    await generate_auto(db)
    await log_action(
        db, "FACILITY_UPDATE", actor=user.username, object_type="facility", object_id=f.id, request=request
    )
    return facility_out(f, with_customer=True)


@router.get("/facilities")
async def list_facilities(
    q: str | None = None,
    pump_manufacturer: str | None = None,
    pump_model: str | None = None,
    facility_type: str | None = None,
    status: str | None = None,
    installed_from: str | None = None,
    installed_to: str | None = None,
    depth_min: float | None = None,
    depth_max: float | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flottvyn. Filtrera t.ex. på pumpmodell för att hitta alla berörda kunder."""
    stmt = select(Facility).join(Customer).order_by(Customer.name)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Facility.facility_no).like(needle),
                func.lower(Facility.pump_model).like(needle),
                func.lower(Facility.pump_manufacturer).like(needle),
                func.lower(Facility.pump_serial).like(needle),
                func.lower(Facility.facility_type).like(needle),
                func.lower(Customer.name).like(needle),
                func.lower(Customer.property_designation).like(needle),
                func.lower(Customer.municipality).like(needle),
            )
        )
    if pump_manufacturer:
        stmt = stmt.where(func.lower(Facility.pump_manufacturer) == pump_manufacturer.lower())
    if pump_model:
        stmt = stmt.where(func.lower(Facility.pump_model) == pump_model.lower())
    if facility_type:
        stmt = stmt.where(Facility.facility_type == facility_type)
    if installed_from:
        stmt = stmt.where(Facility.pump_installed_at >= installed_from)
    if installed_to:
        stmt = stmt.where(Facility.pump_installed_at <= installed_to)
    if depth_min is not None:
        stmt = stmt.where(Facility.total_depth_m >= depth_min)
    if depth_max is not None:
        stmt = stmt.where(Facility.total_depth_m <= depth_max)

    rows = (await db.execute(stmt)).unique().scalars().all()
    out = [facility_out(f, with_customer=True) for f in rows]
    if status:
        out = [f for f in out if f["status"] == status]
    return out


@router.get("/pumps")
async def pump_fleet(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Aggregerad pumpflotta: en rad per tillverkare och modell."""
    stmt = (
        select(
            Facility.pump_manufacturer,
            Facility.pump_model,
            func.count(Facility.id),
            func.min(Facility.pump_installed_at),
            func.max(Facility.pump_installed_at),
        )
        .where(Facility.pump_model != "")
        .group_by(Facility.pump_manufacturer, Facility.pump_model)
        .order_by(func.count(Facility.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "pump_manufacturer": r[0],
            "pump_model": r[1],
            "count": r[2],
            "first_installed": r[3],
            "last_installed": r[4],
        }
        for r in rows
    ]


# ---------- registrering av ny anläggning ----------
@router.post("/new-facility", status_code=201)
async def register_facility(
    payload: NewFacilityIn,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Ett anrop skapar kund (eller återanvänder befintlig), anläggning och första journalraden."""
    if payload.existing_customer_id:
        customer = await get_customer(db, payload.existing_customer_id)
    else:
        customer = Customer(
            customer_no=await next_no(db, Customer, Customer.customer_no, "K", 1000),
            **payload.customer.model_dump(),
        )
        db.add(customer)
        await db.flush()

    facility = Facility(
        facility_no=await next_no(db, Facility, Facility.facility_no, "B", 2000),
        customer_id=customer.id,
        **apply_coordinates(payload.facility.model_dump()),
    )
    db.add(facility)
    await db.flush()
    await auto_koordinat_anlaggning(facility, customer)

    note = JournalEntry(
        customer_id=customer.id,
        facility_id=facility.id,
        entry_type="Registrering",
        title=f"{facility.facility_no} registrerad",
        body=payload.first_note or "Anläggningen registrerades i systemet.",
        author_id=user.id,
        author_name=user.full_name or user.username,
    )
    db.add(note)
    await db.commit()
    await db.refresh(customer)
    await db.refresh(facility)
    await generate_auto(db)
    await log_action(
        db,
        "FACILITY_REGISTER",
        actor=user.username,
        object_type="facility",
        object_id=facility.id,
        request=request,
        detail=f"{customer.customer_no} / {facility.facility_no}",
    )
    return {"customer": customer_out(customer), "facility": facility_out(facility)}


@router.delete("/facilities/{facility_id}", status_code=204)
async def delete_facility(
    facility_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Tar bort en anläggning. Journalen behålls, men lossas från anläggningen."""
    f = (
        await db.execute(select(Facility).where(Facility.id == facility_id))
    ).unique().scalar_one_or_none()
    if f is None:
        raise HTTPException(status_code=404, detail="Anläggningen finns inte")

    from ..models import JournalEntry, Reminder, StoredFile

    for model in (JournalEntry, StoredFile):
        rows = (await db.execute(select(model).where(model.facility_id == facility_id))).scalars().all()
        for row in rows:
            row.facility_id = None
    reminders = (
        await db.execute(select(Reminder).where(Reminder.facility_id == facility_id))
    ).scalars().all()
    for r in reminders:
        await db.delete(r)

    label = f"{f.facility_no} {f.facility_type}"
    await db.delete(f)
    await db.commit()
    await log_action(
        db,
        "FACILITY_DELETE",
        actor=user.username,
        object_type="facility",
        object_id=facility_id,
        request=request,
        detail=label,
    )


@router.post("/facilities/{facility_id}/pump-change")
async def change_pump(
    facility_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Byter pump och skriver en journalrad om bytet, med den gamla pumpen bevarad i texten."""
    f = (
        await db.execute(select(Facility).where(Facility.id == facility_id))
    ).unique().scalar_one_or_none()
    if f is None:
        raise HTTPException(status_code=404, detail="Anläggningen finns inte")

    old = " ".join(x for x in (f.pump_manufacturer, f.pump_model) if x) or "ingen pump"
    if f.pump_serial:
        old += f" (serienr {f.pump_serial})"

    f.pump_manufacturer = (payload.get("pump_manufacturer") or "").strip()
    f.pump_model = (payload.get("pump_model") or "").strip()
    f.pump_serial = (payload.get("pump_serial") or "").strip()
    f.pump_installed_at = payload.get("pump_installed_at") or ""
    f.pump_status = payload.get("pump_status") or "Installerad"
    if payload.get("pump_depth_m") not in (None, ""):
        f.pump_depth_m = float(payload["pump_depth_m"])
    if payload.get("pressure_tank"):
        f.pressure_tank = payload["pressure_tank"]
    if payload.get("reset_service", True):
        f.last_service_at = f.pump_installed_at or f.last_service_at
        f.status = "ok"

    new = " ".join(x for x in (f.pump_manufacturer, f.pump_model) if x) or "ingen pump"
    if f.pump_serial:
        new += f" (serienr {f.pump_serial})"

    from ..models import JournalEntry

    note = payload.get("note", "").strip()
    db.add(
        JournalEntry(
            customer_id=f.customer_id,
            facility_id=f.id,
            entry_type="Pumpbyte",
            title=f"Pumpbyte på {f.facility_no}",
            body=f"Gammal pump: {old}\nNy pump: {new}" + (f"\n\n{note}" if note else ""),
            author_id=user.id,
            author_name=user.full_name or user.username,
        )
    )
    await db.commit()
    await db.refresh(f)
    await log_action(
        db,
        "PUMP_CHANGE",
        actor=user.username,
        object_type="facility",
        object_id=f.id,
        request=request,
        detail=f"{old} -> {new}",
    )
    return facility_out(f, with_customer=True)


@router.delete("/customers/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tar bort kund med allt: anläggningar, journal, filer och påminnelser."""
    import os

    from ..config import settings
    from ..models import StoredFile

    c = await get_customer(db, customer_id)
    files = (
        await db.execute(select(StoredFile).where(StoredFile.customer_id == customer_id))
    ).scalars().all()
    for f in files:
        for path in (
            os.path.join(settings.data_dir, "files", f.stored_name),
            os.path.join(settings.data_dir, "thumbs", f.thumb_name) if f.thumb_name else None,
        ):
            if path and os.path.exists(path):
                os.remove(path)

    label = f"{c.customer_no} {c.name}"
    await db.delete(c)
    await db.commit()
    await log_action(
        db,
        "CUSTOMER_DELETE",
        actor=user.username,
        object_type="customer",
        object_id=customer_id,
        request=request,
        detail=label,
    )
