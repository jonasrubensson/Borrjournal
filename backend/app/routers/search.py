import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, Facility, JournalEntry, StoredFile, User
from ..schemas import customer_out, derived_status, facility_out, journal_out, service_due
from ..security import current_user

router = APIRouter(prefix="/api", tags=["sok"])


@router.get("/search")
async def global_search(
    q: str,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """En sökning som täcker kunder, anläggningar, pumpar, journal och filnamn."""
    needle = f"%{q.lower().strip()}%"
    if len(q.strip()) < 2:
        return {"customers": [], "facilities": [], "journal": [], "files": []}

    customers = (
        await db.execute(
            select(Customer)
            .where(
                or_(
                    func.lower(Customer.name).like(needle),
                    func.lower(Customer.customer_no).like(needle),
                    func.lower(Customer.property_designation).like(needle),
                    func.lower(Customer.municipality).like(needle),
                    func.lower(Customer.phone).like(needle),
                    func.lower(Customer.email).like(needle),
                )
            )
            .order_by(Customer.name)
            .limit(25)
        )
    ).unique().scalars().all()

    facilities = (
        await db.execute(
            select(Facility)
            .join(Customer)
            .where(
                or_(
                    func.lower(Facility.facility_no).like(needle),
                    func.lower(Facility.pump_manufacturer).like(needle),
                    func.lower(Facility.pump_model).like(needle),
                    func.lower(Facility.pump_serial).like(needle),
                    func.lower(Facility.facility_type).like(needle),
                    func.lower(Facility.bedrock_notes).like(needle),
                )
            )
            .limit(50)
        )
    ).unique().scalars().all()

    journal_rows = (
        await db.execute(
            select(JournalEntry, Customer)
            .join(Customer, Customer.id == JournalEntry.customer_id)
            .where(
                or_(
                    func.lower(JournalEntry.title).like(needle),
                    func.lower(JournalEntry.body).like(needle),
                )
            )
            .order_by(JournalEntry.created_at.desc())
            .limit(25)
        )
    ).unique().all()

    file_rows = (
        await db.execute(
            select(StoredFile, Customer)
            .join(Customer, Customer.id == StoredFile.customer_id)
            .where(
                or_(
                    func.lower(StoredFile.filename).like(needle),
                    func.lower(StoredFile.caption).like(needle),
                )
            )
            .limit(25)
        )
    ).unique().all()

    journal = []
    for entry, customer in journal_rows:
        item = journal_out(entry)
        item["customer"] = {"id": customer.id, "name": customer.name}
        journal.append(item)

    files = []
    for f, customer in file_rows:
        item = {"id": f.id, "filename": f.filename, "kind": f.kind, "caption": f.caption}
        item["customer"] = {"id": customer.id, "name": customer.name}
        files.append(item)

    return {
        "customers": [customer_out(c) for c in customers],
        "facilities": [facility_out(f, with_customer=True) for f in facilities],
        "journal": journal,
        "files": files,
    }


@router.get("/dashboard")
async def dashboard(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    facilities = (await db.execute(select(Facility).join(Customer))).unique().scalars().all()
    customer_count = (await db.execute(select(func.count()).select_from(Customer))).scalar() or 0

    items = [facility_out(f, with_customer=True) for f in facilities]
    soon = [f for f in items if f["status"] == "soon"]
    action = [f for f in items if f["status"] == "action"]

    latest_rows = (
        await db.execute(
            select(JournalEntry, Customer)
            .join(Customer, Customer.id == JournalEntry.customer_id)
            .order_by(JournalEntry.created_at.desc())
            .limit(6)
        )
    ).unique().all()
    latest = []
    for entry, customer in latest_rows:
        item = journal_out(entry)
        item["customer"] = {"id": customer.id, "name": customer.name}
        latest.append(item)

    upcoming = sorted(
        [f for f in items if f["service_due"]],
        key=lambda f: f["service_due"],
    )[:8]

    return {
        "counts": {
            "customers": customer_count,
            "facilities": len(items),
            "soon": len(soon),
            "action": len(action),
        },
        "attention": sorted(action + soon, key=lambda f: f["service_due"] or "9999"),
        "upcoming_service": upcoming,
        "latest_journal": latest,
    }


@router.get("/facilities.csv")
async def facilities_csv(
    pump_manufacturer: str | None = None,
    pump_model: str | None = None,
    status: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Exportlista, t.ex. underlag för återkallelse av en pumpmodell."""
    stmt = select(Facility).join(Customer).order_by(Customer.name)
    if pump_manufacturer:
        stmt = stmt.where(func.lower(Facility.pump_manufacturer) == pump_manufacturer.lower())
    if pump_model:
        stmt = stmt.where(func.lower(Facility.pump_model) == pump_model.lower())
    rows = (await db.execute(stmt)).unique().scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Kundnr", "Kund", "Telefon", "E-post", "Fastighet", "Kommun",
            "Anläggning", "Typ", "Djup (m)", "Pumptillverkare", "Pumpmodell",
            "Serienummer", "Installerad", "Senaste service", "Service senast", "Status",
        ]
    )
    for f in rows:
        if status and derived_status(f) != status:
            continue
        c = f.customer
        writer.writerow(
            [
                c.customer_no, c.name, c.phone, c.email, c.property_designation, c.municipality,
                f.facility_no, f.facility_type, f.total_depth_m or "", f.pump_manufacturer,
                f.pump_model, f.pump_serial, f.pump_installed_at, f.last_service_at,
                service_due(f) or "", derived_status(f),
            ]
        )

    buffer.seek(0)
    name = f"anlaggningar-{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter(["\ufeff" + buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/facets")
async def facets(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Värden till filtermenyerna, hämtade från det som faktiskt finns i registret."""

    async def distinct(column):
        rows = (await db.execute(select(column).where(column != "").distinct())).scalars().all()
        return sorted({r for r in rows if r})

    return {
        "manufacturers": await distinct(Facility.pump_manufacturer),
        "models": await distinct(Facility.pump_model),
        "facility_types": await distinct(Facility.facility_type),
        "municipalities": await distinct(Customer.municipality),
        "entry_types": await distinct(JournalEntry.entry_type),
        "horizon": (date.today() + timedelta(days=60)).isoformat(),
    }
