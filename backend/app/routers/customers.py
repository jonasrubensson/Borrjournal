from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, Facility, JournalEntry, User
from ..schemas import (
    CustomerIn,
    FacilityIn,
    OnboardingIn,
    customer_out,
    facility_out,
)
from ..security import current_user, log_action, require_write

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
        **payload.model_dump(),
    )
    db.add(f)
    await db.commit()
    await db.refresh(f)
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
    for key, value in payload.items():
        if key in allowed:
            setattr(f, key, value)
    await db.commit()
    await db.refresh(f)
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


# ---------- onboarding ----------
@router.post("/onboarding", status_code=201)
async def onboarding(
    payload: OnboardingIn,
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
        **payload.facility.model_dump(),
    )
    db.add(facility)
    await db.flush()

    note = JournalEntry(
        customer_id=customer.id,
        facility_id=facility.id,
        entry_type="Registrering",
        title=f"{facility.facility_no} registrerad via onboarding",
        body=payload.first_note or "Anläggningen registrerades i systemet.",
        author_id=user.id,
        author_name=user.full_name or user.username,
    )
    db.add(note)
    await db.commit()
    await db.refresh(customer)
    await db.refresh(facility)
    await log_action(
        db,
        "ONBOARDING",
        actor=user.username,
        object_type="facility",
        object_id=facility.id,
        request=request,
        detail=f"{customer.customer_no} / {facility.facility_no}",
    )
    return {"customer": customer_out(customer), "facility": facility_out(facility)}
