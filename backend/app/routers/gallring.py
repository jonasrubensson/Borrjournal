"""Inaktiva kunder och gallring.

Två närbesläktade frågor med helt olika syfte:

* **Inaktiva kunder** är en affärsfråga. Vem har inte hört av sig på två år och
  är värd ett samtal?
* **Gallring** är en rättslig fråga. Personuppgifter får inte sparas längre än
  nödvändigt, men bokföringsunderlag måste sparas i sju år enligt
  bokföringslagen. De två kraven krockar, och lösningen är att anonymisera i
  stället för att radera: kunduppgifterna tas bort, medan fakturerade
  arbetsorder och brunnens tekniska data står kvar.
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    Customer,
    Facility,
    JournalEntry,
    Quote,
    StoredFile,
    User,
    Visit,
    WorkOrder,
)
from ..schemas import iso_utc
from ..security import current_user, log_action, require_admin
from ..services.numrering import nummerlas_beroende

router = APIRouter(prefix="/api/gallring", tags=["gallring"])

BOKFORING_AR = 7  # bokföringslagen 7 kap 2 §


async def _senaste_handelser(db: AsyncSession) -> dict[str, dict]:
    """Sista aktiviteten per kund, oavsett var den skedde."""
    ut: dict[str, dict] = {}

    def notera(kund_id, nar, vad):
        if not kund_id or not nar:
            return
        text = nar if isinstance(nar, str) else iso_utc(nar)
        if not text:
            return
        dag = text[:10]
        post = ut.setdefault(kund_id, {"datum": "", "vad": ""})
        if dag > post["datum"]:
            post["datum"] = dag
            post["vad"] = vad

    for kund_id, nar in (
        await db.execute(
            select(JournalEntry.customer_id, func.max(JournalEntry.created_at)).group_by(
                JournalEntry.customer_id
            )
        )
    ).all():
        notera(kund_id, nar, "journal")

    for kund_id, nar in (
        await db.execute(
            select(WorkOrder.customer_id, func.max(WorkOrder.created_at)).group_by(
                WorkOrder.customer_id
            )
        )
    ).all():
        notera(kund_id, nar, "arbetsorder")

    for kund_id, nar in (
        await db.execute(
            select(Quote.customer_id, func.max(Quote.created_at)).group_by(Quote.customer_id)
        )
    ).all():
        notera(kund_id, nar, "offert")

    for kund_id, nar in (
        await db.execute(
            select(Visit.customer_id, func.max(Visit.created_at)).group_by(Visit.customer_id)
        )
    ).all():
        notera(kund_id, nar, "besök")

    return ut


async def _bokforing(db: AsyncSession) -> dict[str, dict]:
    """Kunder med fakturerade order, och när den senaste fakturerades."""
    rader = (
        await db.execute(
            select(WorkOrder.customer_id, WorkOrder.invoiced_at, WorkOrder.status).where(
                WorkOrder.status.in_(["fakturerad", "betald"])
            )
        )
    ).all()
    ut: dict[str, dict] = {}
    for kund_id, fakturerad, _status in rader:
        if not kund_id:
            continue
        post = ut.setdefault(kund_id, {"antal": 0, "senast": ""})
        post["antal"] += 1
        if fakturerad and fakturerad > post["senast"]:
            post["senast"] = fakturerad
    return ut


def _ar_sedan(datum: str) -> float | None:
    if not datum:
        return None
    try:
        d = date.fromisoformat(datum[:10])
    except ValueError:
        return None
    return round((date.today() - d).days / 365.25, 1)


@router.get("/review")
async def review(
    inactive_months: int = 24,
    retention_years: int = 7,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Underlag för både uppföljning och gallring i ett svep."""
    kunder = (await db.execute(select(Customer))).unique().scalars().all()
    handelser = await _senaste_handelser(db)
    bokforing = await _bokforing(db)

    idag = date.today()
    grans_inaktiv = (idag - timedelta(days=int(inactive_months * 30.44))).isoformat()
    grans_gallring = (idag - timedelta(days=int(retention_years * 365.25))).isoformat()

    inaktiva, gallringsbara, ut = [], [], []
    for c in kunder:
        h = handelser.get(c.id, {})
        skapad = (iso_utc(c.created_at) or "")[:10]
        senast = h.get("datum") or skapad
        bok = bokforing.get(c.id, {})

        # Bokföringsunderlag måste sparas i sju år från räkenskapsårets slut.
        # Finns en faktura innanför den tiden får kunduppgifterna inte raderas,
        # men de får anonymiseras.
        senaste_faktura = bok.get("senast", "")
        bunden_till = ""
        if senaste_faktura:
            try:
                ar = int(senaste_faktura[:4]) + BOKFORING_AR
                bunden_till = f"{ar}-12-31"
            except ValueError:
                bunden_till = ""
        bunden = bool(bunden_till and bunden_till >= idag.isoformat())

        post = {
            "id": c.id,
            "customer_no": c.customer_no,
            "name": c.name,
            "municipality": c.municipality,
            "created": skapad,
            "last_activity": senast,
            "last_activity_kind": h.get("vad", "inget registrerat"),
            "years_since_activity": _ar_sedan(senast),
            "years_in_register": _ar_sedan(skapad),
            "invoiced_orders": bok.get("antal", 0),
            "last_invoice": senaste_faktura,
            "bookkeeping_until": bunden_till,
            "bookkeeping_locked": bunden,
            "anonymized": bool(c.anonymized_at),
            "recommendation": (
                "behåll"
                if bunden
                else "anonymisera"
                if bok.get("antal")
                else "radera"
            ),
        }
        ut.append(post)
        if senast < grans_inaktiv and not c.anonymized_at:
            inaktiva.append(post)
        if skapad < grans_gallring and senast < grans_gallring and not c.anonymized_at:
            gallringsbara.append(post)

    inaktiva.sort(key=lambda x: x["last_activity"])
    gallringsbara.sort(key=lambda x: x["last_activity"])
    return {
        "inactive_months": inactive_months,
        "retention_years": retention_years,
        "totalt": len(kunder),
        "inaktiva": inaktiva,
        "gallringsbara": gallringsbara,
        "bokforing_ar": BOKFORING_AR,
    }


@router.post("/anonymize")
async def anonymize(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    """Tar bort personuppgifterna men behåller teknik och bokföring.

    Rätt väg när kunden har fakturerade arbeten inom bokföringstiden: uppgifter
    om en identifierbar person försvinner, medan underlaget för räkenskaperna
    och brunnens data står kvar.
    """
    ider = payload.get("ids") or []
    if not ider:
        raise HTTPException(status_code=400, detail="Inga kunder valda")

    kunder = (
        await db.execute(select(Customer).where(Customer.id.in_(ider)))
    ).unique().scalars().all()
    if not kunder:
        raise HTTPException(status_code=404, detail="Hittade inga av de valda kunderna")

    nu = datetime.now(timezone.utc)
    gjorda = []
    for c in kunder:
        if c.anonymized_at:
            continue
        gammalt = f"{c.customer_no} {c.name}"
        c.name = f"Anonymiserad {c.customer_no}"
        c.org_no = ""
        c.phone = ""
        c.email = ""
        c.invoice_address = ""
        c.address = ""
        c.notes = ""
        # Fastighetsbeteckningen är en uppgift om en fastighet, men kan peka ut
        # en person. Kommunen räcker för statistik och behålls.
        c.property_designation = ""
        c.anonymized_at = nu

        # Journaltexter kan innehålla namn och telefonnummer
        rader = (
            await db.execute(select(JournalEntry).where(JournalEntry.customer_id == c.id))
        ).scalars().all()
        for r in rader:
            r.body = "[Anonymiserad enligt GDPR]"
            r.author_name = ""

        # Filer kan vara foton på hus och personer
        filer = (
            await db.execute(select(StoredFile).where(StoredFile.customer_id == c.id))
        ).scalars().all()
        import os

        from ..config import settings as _settings

        for f in filer:
            for katalog, namn in (("files", f.stored_name), ("thumbs", f.thumb_name)):
                if not namn:
                    continue
                vag = os.path.join(_settings.data_dir, katalog, namn)
                if os.path.exists(vag):
                    os.remove(vag)
            await db.delete(f)

        # Mottagaruppgifter på offerter
        offerter = (
            await db.execute(select(Quote).where(Quote.customer_id == c.id))
        ).scalars().all()
        for q in offerter:
            q.recipient_name = c.name
            q.recipient_address = ""
            q.recipient_email = ""

        gjorda.append(gammalt)

    await db.commit()
    await log_action(
        db,
        "CUSTOMER_ANONYMIZE",
        actor=user.username,
        request=request,
        detail=f"{len(gjorda)} kunder: " + ", ".join(gjorda)[:400],
    )
    return {"anonymiserade": len(gjorda), "kunder": gjorda}


@router.post("/bulk-delete")
async def bulk_delete(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    """Raderar kunder helt. Vägrar om bokföringstiden inte gått ut."""
    ider = payload.get("ids") or []
    if not ider:
        raise HTTPException(status_code=400, detail="Inga kunder valda")

    kunder = (
        await db.execute(select(Customer).where(Customer.id.in_(ider)))
    ).unique().scalars().all()
    bokforing = await _bokforing(db)
    idag = date.today().isoformat()

    hindrade, raderade = [], []
    import os

    from ..config import settings as _settings

    for c in kunder:
        bok = bokforing.get(c.id, {})
        if bok.get("senast"):
            try:
                bunden_till = f"{int(bok['senast'][:4]) + BOKFORING_AR}-12-31"
            except ValueError:
                bunden_till = ""
            if bunden_till and bunden_till >= idag:
                hindrade.append(
                    {
                        "customer_no": c.customer_no,
                        "name": c.name,
                        "reason": f"Fakturerat arbete måste sparas till {bunden_till} "
                        "enligt bokföringslagen. Anonymisera i stället.",
                    }
                )
                continue

        filer = (
            await db.execute(select(StoredFile).where(StoredFile.customer_id == c.id))
        ).scalars().all()
        for f in filer:
            for katalog, namn in (("files", f.stored_name), ("thumbs", f.thumb_name)):
                if not namn:
                    continue
                vag = os.path.join(_settings.data_dir, katalog, namn)
                if os.path.exists(vag):
                    os.remove(vag)

        raderade.append(f"{c.customer_no} {c.name}")
        await db.delete(c)

    await db.commit()
    await log_action(
        db,
        "CUSTOMER_BULK_DELETE",
        actor=user.username,
        request=request,
        detail=f"{len(raderade)} raderade, {len(hindrade)} hindrade: "
        + ", ".join(raderade)[:400],
    )
    return {"raderade": len(raderade), "kunder": raderade, "hindrade": hindrade}


@router.get("/facilities-count")
async def facilities_count(
    _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """Antal anläggningar per kund, för att visa vad en radering tar med sig."""
    rader = (
        await db.execute(
            select(Facility.customer_id, func.count()).group_by(Facility.customer_id)
        )
    ).all()
    return {kund_id: antal for kund_id, antal in rader if kund_id}
