import os
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..services.numrering import nummerlas_beroende
from ..models import (
    Article,
    Customer,
    Facility,
    JournalEntry,
    LineItem,
    Quote,
    StockMovement,
    StoredFile,
    User,
    Visit,
    WorkOrder,
)
from ..schemas import iso_utc
from ..security import current_user, log_action, require_admin, require_write
from ..services.pdf import bygg_pdf, kr as belopp_text, summera

router = APIRouter(prefix="/api", tags=["offert och order"])

QUOTE_STATUS = ["utkast", "skickad", "accepterad", "avslagen", "utgangen"]
ORDER_STATUS = ["oppen", "utford", "fakturerad", "betald", "makulerad"]

LOGO_NAMN = "foretagslogotyp.png"


def logo_sokvag() -> str:
    return os.path.join(settings.data_dir, LOGO_NAMN)


FORETAG_STANDARD = {
    "namn": "",
    "adress": "",
    "postnr": "",
    "ort": "",
    "telefon": "",
    "epost": "",
    "orgnr": "",
    "f_skatt": True,
    "villkor": (
        "Betalningsvillkor 30 dagar netto. Dröjsmålsränta enligt räntelagen. "
        "Priset förutsätter framkomlighet för borrigg."
    ),
    "offert_giltig_dagar": 30,
    "betalningsvillkor_dagar": 30,
    # Automatiska påminnelser
    "paminn_obetald_efter_dagar": 7,
    "paminn_offert_efter_dagar": 10,
}


class LineIn(BaseModel):
    name: str = ""
    article_id: str | None = None
    kind: str = "material"
    article_no: str = ""
    note: str = ""
    unit: str = "st"
    quantity: float = 1.0
    unit_price: float = 0.0
    vat_percent: float = 25.0
    discount_percent: float = 0.0
    position: int = 0


def rad_ut(r: LineItem) -> dict:
    radsumma = r.quantity * r.unit_price * (1 - (r.discount_percent or 0) / 100)
    return {
        "id": r.id,
        "article_id": r.article_id,
        "position": r.position,
        "kind": r.kind,
        "article_no": r.article_no,
        "name": r.name,
        "note": r.note,
        "unit": r.unit,
        "quantity": r.quantity,
        "unit_price": r.unit_price,
        "vat_percent": r.vat_percent,
        "discount_percent": r.discount_percent,
        "line_total": round(radsumma, 2),
    }


async def _rader(db: AsyncSession, *, quote_id=None, work_order_id=None) -> list[LineItem]:
    stmt = select(LineItem).order_by(LineItem.position, LineItem.name)
    stmt = (
        stmt.where(LineItem.quote_id == quote_id)
        if quote_id
        else stmt.where(LineItem.work_order_id == work_order_id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _foretag(db: AsyncSession) -> dict:
    from ..services.notify import get_setting

    conf = await get_setting(db, "foretag", FORETAG_STANDARD)
    conf["logotyp"] = logo_sokvag() if os.path.exists(logo_sokvag()) else ""
    return conf


async def _nasta(db: AsyncSession, model, kolumn, prefix: str) -> str:
    from ..services.numrering import nasta_nummer, nummerlas_beroende

    return await nasta_nummer(db, model, kolumn, prefix, 1000)


# ---------------- företagsuppgifter ----------------
@router.get("/company")
async def read_company(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    conf = await _foretag(db)
    conf["har_logotyp"] = os.path.exists(logo_sokvag())
    return conf


@router.get("/company/logo")
async def company_logo(db: AsyncSession = Depends(get_db)):
    """Öppen utan inloggning, eftersom den visas på inloggningssidan."""
    from fastapi.responses import FileResponse

    if not os.path.exists(logo_sokvag()):
        raise HTTPException(status_code=404, detail="Ingen logotyp uppladdad")
    return FileResponse(logo_sokvag(), media_type="image/png")


@router.post("/company/logo")
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tar emot logotypen och normaliserar den till PNG med rimlig storlek."""
    import io as _io

    from PIL import Image, UnidentifiedImageError

    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Bilden är större än 8 MB")
    try:
        bild = Image.open(_io.BytesIO(raw))
        bild.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(
            status_code=415, detail="Kunde inte läsa bilden. Använd PNG, JPG eller WEBP."
        ) from None

    # Genomskinlighet bevaras, så att en logotyp med transparent bakgrund
    # inte får en vit ruta runt sig i PDF:en.
    if bild.mode not in ("RGBA", "RGB"):
        bild = bild.convert("RGBA")
    bild.thumbnail((900, 400))
    os.makedirs(settings.data_dir, exist_ok=True)
    bild.save(logo_sokvag(), "PNG")

    await log_action(db, "LOGO_UPLOAD", actor=user.username, request=request)
    return {"ok": True, "bredd": bild.width, "hojd": bild.height}


@router.delete("/company/logo", status_code=204)
async def delete_logo(
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if os.path.exists(logo_sokvag()):
        os.remove(logo_sokvag())
    await log_action(db, "LOGO_DELETE", actor=user.username, request=request)


@router.put("/company")
async def write_company(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from ..services.notify import save_setting

    conf = await _foretag(db)
    for nyckel in FORETAG_STANDARD:
        if nyckel in payload:
            conf[nyckel] = payload[nyckel]
    await save_setting(db, "foretag", conf)
    await log_action(db, "COMPANY_UPDATE", actor=user.username, request=request)
    return conf


# ---------------- offertmallar ----------------
def mall_ut(m) -> dict:
    from ..services.templates import rader_ur

    rader = rader_ur(m)
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "title": m.title,
        "intro": m.intro,
        "terms": m.terms,
        "valid_days": m.valid_days,
        "lines": rader,
        "line_count": len(rader),
        "is_builtin": m.is_builtin,
        "estimate": round(
            sum(r.get("quantity", 0) * r.get("unit_price", 0) * 1.25 for r in rader), 2
        ),
    }


@router.get("/quote-templates")
async def list_templates(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from ..models import QuoteTemplate
    from ..services.templates import se_till_att_mallar_finns

    await se_till_att_mallar_finns(db)
    mallar = (
        await db.execute(select(QuoteTemplate).order_by(QuoteTemplate.sort_order, QuoteTemplate.name))
    ).scalars().all()
    return [mall_ut(m) for m in mallar]


@router.post("/quote-templates", status_code=201)
async def create_template(
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Ny mall, antingen tom eller sparad från en befintlig offert."""
    import json as _json

    from ..models import QuoteTemplate

    namn = (payload.get("name") or "").strip()
    if not namn:
        raise HTTPException(status_code=400, detail="Mallen behöver ett namn")

    rader = payload.get("lines") or []
    if payload.get("from_quote_id"):
        q = await _hamta_offert(db, payload["from_quote_id"])
        rader = [
            {
                "kind": r.kind,
                "article_no": r.article_no,
                "name": r.name,
                "note": r.note,
                "unit": r.unit,
                "quantity": r.quantity,
                "unit_price": r.unit_price,
                "vat_percent": r.vat_percent,
            }
            for r in await _rader(db, quote_id=q.id)
        ]
        payload.setdefault("title", q.title)
        payload.setdefault("intro", q.intro)
        payload.setdefault("terms", q.terms)

    m = QuoteTemplate(
        name=namn,
        description=payload.get("description", ""),
        title=payload.get("title", ""),
        intro=payload.get("intro", ""),
        terms=payload.get("terms", ""),
        valid_days=int(payload.get("valid_days") or 30),
        lines=_json.dumps(rader, ensure_ascii=False),
        sort_order=99,
        created_by=user.full_name or user.username,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    await log_action(
        db, "TEMPLATE_CREATE", actor=user.username, object_type="template", object_id=m.id,
        request=request, detail=m.name,
    )
    return mall_ut(m)


@router.patch("/quote-templates/{template_id}")
async def update_template(
    template_id: str,
    payload: dict,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    import json as _json

    from ..models import QuoteTemplate

    m = (
        await db.execute(select(QuoteTemplate).where(QuoteTemplate.id == template_id))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Mallen finns inte")
    for falt in ("name", "description", "title", "intro", "terms"):
        if falt in payload:
            setattr(m, falt, payload[falt])
    if "valid_days" in payload:
        m.valid_days = int(payload["valid_days"] or 30)
    if "lines" in payload:
        m.lines = _json.dumps(payload["lines"], ensure_ascii=False)
    # En ändrad standardmall är inte längre standard, den är er egen
    if not payload.get("behall_builtin"):
        m.is_builtin = False
    await db.commit()
    await db.refresh(m)
    return mall_ut(m)


@router.delete("/quote-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    from ..models import QuoteTemplate

    m = (
        await db.execute(select(QuoteTemplate).where(QuoteTemplate.id == template_id))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Mallen finns inte")
    namn = m.name
    await db.delete(m)
    await db.commit()
    await log_action(
        db, "TEMPLATE_DELETE", actor=user.username, object_type="template",
        object_id=template_id, request=request, detail=namn,
    )


# ---------------- offerter ----------------
def quote_ut(q: Quote, rader: list[LineItem], kundnamn: str = "") -> dict:
    r = [rad_ut(x) for x in rader]
    total = summera(r, q.discount_percent)
    return {
        "id": q.id,
        "quote_no": q.quote_no,
        "status": q.status,
        "title": q.title,
        "intro": q.intro,
        "terms": q.terms,
        "customer_id": q.customer_id,
        "customer_name": kundnamn,
        "facility_id": q.facility_id,
        "visit_id": q.visit_id,
        "recipient_name": q.recipient_name,
        "recipient_address": q.recipient_address,
        "recipient_email": q.recipient_email,
        "valid_until": q.valid_until,
        "discount_percent": q.discount_percent,
        "rot_deduction": q.rot_deduction,
        "sent_at": iso_utc(q.sent_at),
        "sent_to": q.sent_to,
        "decided_at": q.decided_at,
        "created_at": iso_utc(q.created_at),
        "created_by": q.created_by,
        "lines": r,
        "totals": total,
    }


@router.get("/quotes")
async def list_quotes(
    customer_id: str | None = None,
    visit_id: str | None = None,
    status: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Quote).order_by(Quote.created_at.desc())
    if customer_id:
        stmt = stmt.where(Quote.customer_id == customer_id)
    if visit_id:
        stmt = stmt.where(Quote.visit_id == visit_id)
    if status == "aktiva":
        stmt = stmt.where(Quote.status.in_(["utkast", "skickad"]))
    elif status:
        stmt = stmt.where(Quote.status == status)

    offerter = (await db.execute(stmt)).scalars().all()
    namn = {}
    if offerter:
        rader = (
            await db.execute(
                select(Customer.id, Customer.name).where(
                    Customer.id.in_([q.customer_id for q in offerter if q.customer_id])
                )
            )
        ).all()
        namn = {r[0]: r[1] for r in rader}
    return [
        quote_ut(q, await _rader(db, quote_id=q.id), namn.get(q.customer_id, ""))
        for q in offerter
    ]


@router.post("/quotes", status_code=201)
async def create_quote(
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    """Skapar en offert på en kund eller ett platsbesök."""
    customer_id = payload.get("customer_id")
    visit_id = payload.get("visit_id")
    # En offert kan stå för sig själv. Ringer någon och vill ha ett pris ska man
    # slippa lägga upp en kund först, för det blir kanske aldrig någon affär.
    if not customer_id and not visit_id and not (payload.get("recipient_name") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Ange kund, platsbesök eller åtminstone vem offerten ska till",
        )

    foretag = await _foretag(db)
    mottagare = {"namn": "", "adress": "", "epost": ""}

    if not customer_id and not visit_id:
        mottagare = {
            "namn": payload.get("recipient_name", ""),
            "adress": payload.get("recipient_address", ""),
            "epost": payload.get("recipient_email", ""),
        }
    elif customer_id:
        c = (
            await db.execute(select(Customer).where(Customer.id == customer_id))
        ).unique().scalar_one_or_none()
        if c is None:
            raise HTTPException(status_code=404, detail="Kunden finns inte")
        mottagare = {
            "namn": c.name,
            "adress": c.invoice_address or c.address or "",
            "epost": c.email or "",
        }
    else:
        v = (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one_or_none()
        if v is None:
            raise HTTPException(status_code=404, detail="Besöket finns inte")
        mottagare = {
            "namn": v.contact_name or v.property_designation or "",
            "adress": v.address or "",
            "epost": v.email or "",
        }

    giltig = payload.get("valid_until")
    if not giltig:
        dagar = int(foretag.get("offert_giltig_dagar") or 30)
        giltig = date.fromordinal(date.today().toordinal() + dagar).isoformat()

    # Mall vald: hämta rubrik, texter, giltighetstid och rader därifrån
    mall = None
    mallrader = []
    if payload.get("template_id"):
        from ..models import QuoteTemplate
        from ..services.templates import rader_ur

        mall = (
            await db.execute(select(QuoteTemplate).where(QuoteTemplate.id == payload["template_id"]))
        ).scalar_one_or_none()
        if mall is None:
            raise HTTPException(status_code=404, detail="Mallen finns inte")
        mallrader = rader_ur(mall)
        giltig = date.fromordinal(date.today().toordinal() + (mall.valid_days or 30)).isoformat()

    q = Quote(
        quote_no=await _nasta(db, Quote, Quote.quote_no, "OFF"),
        customer_id=customer_id,
        visit_id=visit_id,
        facility_id=payload.get("facility_id"),
        title=payload.get("title") or (mall.title if mall else ""),
        intro=payload.get("intro") or (mall.intro if mall else ""),
        terms=payload.get("terms") or (mall.terms if mall else "") or foretag.get("villkor", ""),
        recipient_name=payload.get("recipient_name") or mottagare["namn"],
        recipient_address=payload.get("recipient_address") or mottagare["adress"],
        recipient_email=payload.get("recipient_email") or mottagare["epost"],
        valid_until=giltig,
        created_by=user.full_name or user.username,
    )
    db.add(q)
    await db.flush()

    # Mallens rader matchas mot artikelregistret på nummer, så att dagens pris används
    for i, r in enumerate(mallrader):
        artikel = None
        if r.get("article_no"):
            artikel = (
                await db.execute(select(Article).where(Article.article_no == r["article_no"]))
            ).scalar_one_or_none()
        if artikel is None and r.get("name"):
            artikel = (
                await db.execute(
                    select(Article).where(
                        func.lower(Article.name) == r["name"].lower(), Article.is_active.is_(True)
                    )
                )
            ).scalar_one_or_none()

        db.add(
            LineItem(
                quote_id=q.id,
                article_id=artikel.id if artikel else None,
                position=i,
                kind=r.get("kind", "material"),
                article_no=artikel.article_no if artikel else r.get("article_no", ""),
                name=r.get("name", ""),
                note=r.get("note", ""),
                unit=(artikel.unit if artikel else r.get("unit", "st")),
                quantity=r.get("quantity", 1),
                unit_price=(artikel.sales_price if artikel else r.get("unit_price", 0)),
                vat_percent=(artikel.vat_percent if artikel else r.get("vat_percent", 25)),
            )
        )
    await db.commit()
    await db.refresh(q)
    await log_action(
        db, "QUOTE_CREATE", actor=user.username, object_type="quote", object_id=q.id,
        request=request, detail=f"{q.quote_no}" + (f" ur mall {mall.name}" if mall else ""),
    )
    return quote_ut(q, await _rader(db, quote_id=q.id))


async def _hamta_offert(db: AsyncSession, quote_id: str) -> Quote:
    q = (await db.execute(select(Quote).where(Quote.id == quote_id))).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="Offerten finns inte")
    return q


@router.get("/quotes/{quote_id}")
async def read_quote(
    quote_id: str, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    q = await _hamta_offert(db, quote_id)
    namn = ""
    if q.customer_id:
        namn = (
            await db.execute(select(Customer.name).where(Customer.id == q.customer_id))
        ).scalar() or ""
    return quote_ut(q, await _rader(db, quote_id=q.id), namn)


@router.patch("/quotes/{quote_id}")
async def update_quote(
    quote_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    q = await _hamta_offert(db, quote_id)
    if payload.get("status") and payload["status"] not in QUOTE_STATUS:
        raise HTTPException(status_code=400, detail="Okänd status")
    for falt in (
        "status", "title", "intro", "terms", "recipient_name", "recipient_address",
        "recipient_email", "valid_until", "discount_percent", "rot_deduction", "facility_id",
    ):
        if falt in payload:
            setattr(q, falt, payload[falt])
    if payload.get("status") in ("accepterad", "avslagen") and not q.decided_at:
        q.decided_at = date.today().isoformat()
    await db.commit()
    await db.refresh(q)
    await log_action(
        db, "QUOTE_UPDATE", actor=user.username, object_type="quote", object_id=q.id,
        request=request, detail=f"{q.quote_no} {q.status}",
    )
    return quote_ut(q, await _rader(db, quote_id=q.id))


@router.post("/quotes/{quote_id}/to-customer", status_code=201)
async def quote_to_customer(
    quote_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    """Gör en kund av en fristående offert när den lett till affär."""
    from .customers import next_no

    q = await _hamta_offert(db, quote_id)
    if q.customer_id:
        raise HTTPException(status_code=409, detail="Offerten hör redan till en kund")

    kund = Customer(
        customer_no=await next_no(db, Customer, Customer.customer_no, "K", 1000),
        name=payload.get("name") or q.recipient_name or "Ny kund",
        customer_type=payload.get("customer_type", "Privat"),
        email=q.recipient_email or "",
        phone=payload.get("phone", ""),
        address=q.recipient_address or "",
        property_designation=payload.get("property_designation", ""),
        municipality=payload.get("municipality", ""),
    )
    db.add(kund)
    await db.flush()

    anlaggning = None
    if payload.get("create_facility", True):
        anlaggning = Facility(
            facility_no=await next_no(db, Facility, Facility.facility_no, "B", 2000),
            customer_id=kund.id,
            facility_type=payload.get("facility_type", "Bergborrad brunn"),
            pump_status="Ska installeras",
        )
        db.add(anlaggning)
        await db.flush()

    q.customer_id = kund.id
    if anlaggning is not None:
        q.facility_id = anlaggning.id

    db.add(
        JournalEntry(
            customer_id=kund.id,
            facility_id=anlaggning.id if anlaggning else None,
            entry_type="Registrering",
            title=f"Kund skapad från offert {q.quote_no}",
            body=(q.title or "") + (f"\nSkickad till {q.sent_to}" if q.sent_to else ""),
            author_id=user.id,
            author_name=user.full_name or user.username,
        )
    )
    await db.commit()
    await db.refresh(kund)
    await log_action(
        db, "QUOTE_TO_CUSTOMER", actor=user.username, object_type="quote", object_id=q.id,
        request=request, detail=f"{q.quote_no} -> {kund.customer_no}",
    )
    from ..schemas import customer_out, facility_out

    return {
        "customer": customer_out(kund),
        "facility": facility_out(anlaggning) if anlaggning else None,
    }


@router.delete("/quotes/{quote_id}", status_code=204)
async def delete_quote(
    quote_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    q = await _hamta_offert(db, quote_id)
    nummer = q.quote_no
    await db.delete(q)
    await db.commit()
    await log_action(
        db, "QUOTE_DELETE", actor=user.username, object_type="quote", object_id=quote_id,
        request=request, detail=nummer,
    )


# ---------------- rader ----------------
async def _lagg_rad(db: AsyncSession, payload: LineIn, *, quote_id=None, work_order_id=None):
    data = payload.model_dump()
    artikel_id = data.pop("article_id", None)

    if artikel_id:
        a = (
            await db.execute(select(Article).where(Article.id == artikel_id))
        ).scalar_one_or_none()
        if a is None:
            raise HTTPException(status_code=404, detail="Artikeln finns inte")
        # Kopiera från artikeln. Ändrat pris senare ska inte ändra gamla order.
        data["name"] = data["name"] or a.name
        data["article_no"] = data["article_no"] or a.article_no
        data["unit"] = data["unit"] if data["unit"] != "st" else a.unit
        if not data["unit_price"]:
            data["unit_price"] = a.sales_price
        data["vat_percent"] = a.vat_percent
        if not data["kind"] or data["kind"] == "material":
            data["kind"] = "arbete" if (a.category or "").lower().startswith("arbete") else "material"

    if not (data.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="Raden behöver en benämning")

    rad = LineItem(quote_id=quote_id, work_order_id=work_order_id, article_id=artikel_id, **data)
    db.add(rad)
    await db.commit()
    await db.refresh(rad)
    return rad


@router.get("/articles/match")
async def matcha_artikel(
    name: str,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Letar efter en artikel som liknar en fritextrad.

    Används för att varna innan man skriver in något som redan finns, och för att
    kunna erbjuda att lägga upp raden som artikel om den saknas.
    """
    import difflib

    text = (name or "").strip().lower()
    if len(text) < 3:
        return {"traffar": []}

    artiklar = (
        await db.execute(select(Article).where(Article.is_active.is_(True)))
    ).scalars().all()
    traffar = []
    for a in artiklar:
        namn = (a.name or "").lower()
        if not namn:
            continue
        likhet = difflib.SequenceMatcher(None, text, namn).ratio()
        if text in namn or namn in text:
            likhet = max(likhet, 0.9)
        if likhet >= 0.62:
            traffar.append(
                {
                    "id": a.id,
                    "article_no": a.article_no,
                    "name": a.name,
                    "unit": a.unit,
                    "sales_price": a.sales_price,
                    "stock": a.stock if a.track_stock else None,
                    "likhet": round(likhet, 2),
                }
            )
    traffar.sort(key=lambda x: -x["likhet"])
    return {"traffar": traffar[:4]}


@router.post("/quotes/{quote_id}/lines", status_code=201)
async def add_quote_line(
    quote_id: str,
    payload: LineIn,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    await _hamta_offert(db, quote_id)
    return rad_ut(await _lagg_rad(db, payload, quote_id=quote_id))


@router.patch("/lines/{line_id}")
async def update_line(
    line_id: str,
    payload: dict,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    rad = (await db.execute(select(LineItem).where(LineItem.id == line_id))).scalar_one_or_none()
    if rad is None:
        raise HTTPException(status_code=404, detail="Raden finns inte")
    for falt in LineIn.model_fields:
        if falt in payload and falt != "article_id":
            setattr(rad, falt, payload[falt])
    await db.commit()
    await db.refresh(rad)
    return rad_ut(rad)


@router.delete("/lines/{line_id}", status_code=204)
async def delete_line(
    line_id: str, user: User = Depends(require_write), db: AsyncSession = Depends(get_db)
):
    rad = (await db.execute(select(LineItem).where(LineItem.id == line_id))).scalar_one_or_none()
    if rad is None:
        raise HTTPException(status_code=404, detail="Raden finns inte")
    await db.delete(rad)
    await db.commit()


# ---------------- PDF ----------------
async def _bygg_dokument(db: AsyncSession, *, offert: Quote = None, order: WorkOrder = None) -> bytes:
    foretag = await _foretag(db)
    if offert is not None:
        rader = [rad_ut(r) for r in await _rader(db, quote_id=offert.id)]
        fastighet = ""
        if offert.facility_id:
            f = (
                await db.execute(select(Facility).where(Facility.id == offert.facility_id))
            ).unique().scalar_one_or_none()
            if f is not None and f.customer is not None:
                fastighet = f"Fastighet: {f.customer.property_designation}"
        return bygg_pdf(
            typ="Offert",
            nummer=offert.quote_no,
            titel=offert.title,
            foretag=foretag,
            mottagare={
                "namn": offert.recipient_name,
                "adress": offert.recipient_address,
                "fastighet": fastighet,
                "epost": offert.recipient_email,
            },
            rader=rader,
            intro=offert.intro,
            villkor=offert.terms,
            datum=iso_utc(offert.created_at)[:10] if offert.created_at else "",
            giltig_till=offert.valid_until,
            rabatt_procent=offert.discount_percent,
        )

    rader = [rad_ut(r) for r in await _rader(db, work_order_id=order.id)]
    c = None
    if order.customer_id:
        c = (
            await db.execute(select(Customer).where(Customer.id == order.customer_id))
        ).unique().scalar_one_or_none()
    anlaggning = ""
    if order.facility_id:
        f = (
            await db.execute(select(Facility).where(Facility.id == order.facility_id))
        ).unique().scalar_one_or_none()
        if f is not None:
            anlaggning = f"{f.facility_no} {f.facility_type}"
    return bygg_pdf(
        typ="Arbetsorder",
        nummer=order.order_no,
        titel=order.title,
        foretag=foretag,
        mottagare={
            "namn": c.name if c else "Kund ej vald",
            "adress": (c.invoice_address or c.address) if c else "",
            "fastighet": f"Fastighet: {c.property_designation}" if c and c.property_designation else "",
            "telefon": c.phone if c else "",
        },
        rader=rader,
        intro=order.description,
        villkor="",
        datum=order.performed_at or (iso_utc(order.created_at)[:10] if order.created_at else ""),
        referens=anlaggning,
        rabatt_procent=order.discount_percent,
        fotnot=(
            f"Utförd {order.performed_at} av {order.performed_by}."
            if order.performed_at
            else ""
        ),
    )


@router.get("/quotes/{quote_id}/pdf")
async def quote_pdf(
    quote_id: str,
    ladda_ner: bool = False,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await _hamta_offert(db, quote_id)
    pdf = await _bygg_dokument(db, offert=q)
    disp = "attachment" if ladda_ner else "inline"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp}; filename="{q.quote_no}.pdf"'},
    )


async def _spara_som_dokument(
    db: AsyncSession, *, customer_id: str, filnamn: str, pdf: bytes, user: User,
    facility_id: str | None = None, caption: str = "",
) -> StoredFile | None:
    """Lägger PDF:en bland kundens dokument, så att det som skickades finns kvar."""
    if not customer_id:
        return None
    os.makedirs(os.path.join(settings.data_dir, "files"), exist_ok=True)
    lagrat = f"{uuid.uuid4()}.pdf"
    with open(os.path.join(settings.data_dir, "files", lagrat), "wb") as fh:
        fh.write(pdf)

    from ..routers.files import make_pdf_thumb

    post = StoredFile(
        customer_id=customer_id,
        facility_id=facility_id,
        filename=filnamn,
        stored_name=lagrat,
        thumb_name=make_pdf_thumb(pdf, lagrat),
        content_type="application/pdf",
        kind="dokument",
        size_bytes=len(pdf),
        caption=caption,
        uploaded_by=user.full_name or user.username,
    )
    db.add(post)
    return post


@router.post("/quotes/{quote_id}/send")
async def send_quote(
    quote_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Mejlar offerten som PDF och sparar den bland kundens dokument."""
    from ..services.notify import DEFAULT_SMTP, SMTP_KEY, get_setting, send_email

    q = await _hamta_offert(db, quote_id)
    mottagare = (payload.get("recipient") or q.recipient_email or "").strip()
    if "@" not in mottagare:
        raise HTTPException(status_code=400, detail="Ange en giltig e-postadress")
    rader = await _rader(db, quote_id=q.id)
    if not rader:
        raise HTTPException(status_code=400, detail="Offerten har inga rader att skicka")

    pdf = await _bygg_dokument(db, offert=q)
    foretag = await _foretag(db)
    total = summera([rad_ut(r) for r in rader], q.discount_percent)

    # Vanligt bindestreck i ämnesraden. Tankstreck tvingar fram MIME-kodning av
    # hela raden, vilket ser konstigt ut i vissa klienter.
    amne = payload.get("subject") or f"Offert {q.quote_no} - {q.title or 'brunnsborrning'}"
    brodtext = payload.get("message") or (
        f"Hej,\n\nBifogat finner ni offert {q.quote_no}"
        + (f" avseende {q.title}" if q.title else "")
        + f".\n\nSumma inklusive moms: {belopp_text(total['brutto'])} kr"
        + (f"\nOfferten gäller till {q.valid_until}." if q.valid_until else "")
        + f"\n\nHör gärna av er vid frågor.\n\nMed vänlig hälsning\n{foretag.get('namn', '')}\n"
    )

    smtp = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    try:
        await send_email(
            {**smtp, "enabled": True},
            amne,
            brodtext,
            [mottagare],
            attachments=[(f"{q.quote_no}.pdf", pdf, "application/pdf")],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"Kunde inte skicka: {exc}. Kontrollera e-postinställningarna."
        ) from exc

    q.status = "skickad" if q.status == "utkast" else q.status
    q.sent_at = datetime.now(timezone.utc)
    q.sent_to = mottagare

    sparad = await _spara_som_dokument(
        db,
        customer_id=q.customer_id,
        filnamn=f"{q.quote_no} offert.pdf",
        pdf=pdf,
        user=user,
        facility_id=q.facility_id,
        caption=f"Offert skickad till {mottagare}",
    )
    if q.customer_id:
        db.add(
            JournalEntry(
                customer_id=q.customer_id,
                facility_id=q.facility_id,
                entry_type="Offert",
                title=f"Offert {q.quote_no} skickad",
                body=f"Skickad till {mottagare}. Summa inkl. moms {belopp_text(total['brutto'])} kr."
                + (f" Gäller till {q.valid_until}." if q.valid_until else ""),
                author_id=user.id,
                author_name=user.full_name or user.username,
            )
        )
    await db.commit()
    await log_action(
        db, "QUOTE_SENT", actor=user.username, object_type="quote", object_id=q.id,
        request=request, detail=f"{q.quote_no} till {mottagare}",
    )
    return {
        "ok": True,
        "recipient": mottagare,
        "file_id": sparad.id if sparad else None,
        "saved_to_customer": bool(sparad),
    }


# ---------------- arbetsorder ----------------
def order_ut(o: WorkOrder, rader: list[LineItem], kundnamn: str = "") -> dict:
    r = [rad_ut(x) for x in rader]
    total = summera(r, o.discount_percent)
    material = sum(x["line_total"] for x in r if x["kind"] == "material")
    arbete = sum(x["line_total"] for x in r if x["kind"] == "arbete")
    return {
        "id": o.id,
        "order_no": o.order_no,
        "status": o.status,
        "title": o.title,
        "description": o.description,
        "customer_id": o.customer_id,
        "customer_name": kundnamn,
        "saknar_kund": not o.customer_id,
        "facility_id": o.facility_id,
        "quote_id": o.quote_id,
        "performed_at": o.performed_at,
        "performed_by": o.performed_by,
        "invoiced_at": o.invoiced_at,
        "invoice_no": o.invoice_no,
        "paid_at": o.paid_at,
        "discount_percent": o.discount_percent,
        "rot_deduction": o.rot_deduction,
        "stock_deducted": o.stock_deducted,
        "created_at": iso_utc(o.created_at),
        "created_by": o.created_by,
        "lines": r,
        "totals": total,
        "material_total": round(material, 2),
        "labour_total": round(arbete, 2),
    }


async def _hamta_order(db: AsyncSession, order_id: str) -> WorkOrder:
    o = (await db.execute(select(WorkOrder).where(WorkOrder.id == order_id))).scalar_one_or_none()
    if o is None:
        raise HTTPException(status_code=404, detail="Arbetsordern finns inte")
    return o


@router.get("/work-orders")
async def list_orders(
    customer_id: str | None = None,
    status: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(WorkOrder).order_by(WorkOrder.created_at.desc())
    if customer_id:
        stmt = stmt.where(WorkOrder.customer_id == customer_id)
    if status == "att_fakturera":
        stmt = stmt.where(WorkOrder.status == "utford")
    elif status == "obetalda":
        stmt = stmt.where(WorkOrder.status == "fakturerad")
    elif status == "aktiva":
        stmt = stmt.where(WorkOrder.status.in_(["oppen", "utford", "fakturerad"]))
    elif status:
        stmt = stmt.where(WorkOrder.status == status)

    order = (await db.execute(stmt)).scalars().all()
    namn = {}
    if order:
        rader = (
            await db.execute(
                select(Customer.id, Customer.name).where(
                    Customer.id.in_([o.customer_id for o in order if o.customer_id])
                )
            )
        ).all()
        namn = {r[0]: r[1] for r in rader}
    return [
        order_ut(o, await _rader(db, work_order_id=o.id), namn.get(o.customer_id, ""))
        for o in order
    ]


@router.get("/work-orders/summary")
async def order_summary(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Vad som väntar på fakturering och vad som väntar på betalning."""
    alla = (await db.execute(select(WorkOrder))).scalars().all()
    ut = {"att_fakturera": 0, "att_fakturera_belopp": 0.0, "obetalda": 0, "obetalda_belopp": 0.0,
          "oppna": 0, "utan_kund": 0}
    for o in alla:
        rader = [rad_ut(r) for r in await _rader(db, work_order_id=o.id)]
        belopp = summera(rader, o.discount_percent)["brutto"]
        if o.status == "utford":
            ut["att_fakturera"] += 1
            ut["att_fakturera_belopp"] += belopp
        elif o.status == "fakturerad":
            ut["obetalda"] += 1
            ut["obetalda_belopp"] += belopp
        elif o.status == "oppen":
            ut["oppna"] += 1
        if not o.customer_id:
            ut["utan_kund"] += 1
    ut["att_fakturera_belopp"] = round(ut["att_fakturera_belopp"], 2)
    ut["obetalda_belopp"] = round(ut["obetalda_belopp"], 2)
    return ut


@router.post("/work-orders", status_code=201)
async def create_order(
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    # Kunden får väljas senare. Det gör det möjligt att börja skriva rader
    # direkt på plats och koppla ordern till rätt kund efteråt.
    customer_id = payload.get("customer_id")
    c = None
    if customer_id:
        c = (
            await db.execute(select(Customer).where(Customer.id == customer_id))
        ).unique().scalar_one_or_none()
        if c is None:
            raise HTTPException(status_code=404, detail="Kunden finns inte")

    o = WorkOrder(
        order_no=await _nasta(db, WorkOrder, WorkOrder.order_no, "AO"),
        customer_id=customer_id,
        facility_id=payload.get("facility_id"),
        quote_id=payload.get("quote_id"),
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        performed_at=payload.get("performed_at", ""),
        performed_by=payload.get("performed_by") or (user.full_name or user.username),
        created_by=user.full_name or user.username,
    )
    db.add(o)
    await db.flush()

    # Raderna från en accepterad offert följer med, så inget skrivs in två gånger
    if payload.get("quote_id") and payload.get("copy_quote_lines", True):
        for r in await _rader(db, quote_id=payload["quote_id"]):
            db.add(
                LineItem(
                    work_order_id=o.id,
                    article_id=r.article_id,
                    position=r.position,
                    kind=r.kind,
                    article_no=r.article_no,
                    name=r.name,
                    note=r.note,
                    unit=r.unit,
                    quantity=r.quantity,
                    unit_price=r.unit_price,
                    vat_percent=r.vat_percent,
                    discount_percent=r.discount_percent,
                )
            )
    await db.commit()
    await db.refresh(o)
    await log_action(
        db, "ORDER_CREATE", actor=user.username, object_type="work_order", object_id=o.id,
        request=request, detail=o.order_no,
    )
    return order_ut(o, await _rader(db, work_order_id=o.id), c.name if c else "")


@router.get("/work-orders/{order_id}")
async def read_order(
    order_id: str, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    o = await _hamta_order(db, order_id)
    namn = ""
    if o.customer_id:
        namn = (
            await db.execute(select(Customer.name).where(Customer.id == o.customer_id))
        ).scalar() or ""
    return order_ut(o, await _rader(db, work_order_id=o.id), namn)


@router.post("/work-orders/{order_id}/lines", status_code=201)
async def add_order_line(
    order_id: str,
    payload: LineIn,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    o = await _hamta_order(db, order_id)
    if o.status in ("fakturerad", "betald"):
        raise HTTPException(
            status_code=400,
            detail="Ordern är fakturerad. Ändra status först om något ska läggas till.",
        )
    return rad_ut(await _lagg_rad(db, payload, work_order_id=order_id))


async def _dra_lager(db: AsyncSession, o: WorkOrder, user: User) -> int:
    """Drar materialet från lagret när ordern markeras utförd. En gång."""
    if o.stock_deducted:
        return 0
    antal = 0
    for r in await _rader(db, work_order_id=o.id):
        if not r.article_id or r.kind != "material":
            continue
        a = (
            await db.execute(select(Article).where(Article.id == r.article_id))
        ).scalar_one_or_none()
        if a is None or not a.track_stock:
            continue
        a.stock = round((a.stock or 0) - r.quantity, 3)
        db.add(
            StockMovement(
                article_id=a.id,
                change=-r.quantity,
                balance_after=a.stock,
                reason="forbrukning",
                work_order_id=o.id,
                note=f"{o.order_no} {r.name}"[:255],
                by_user=user.full_name or user.username,
            )
        )
        antal += 1
    o.stock_deducted = True
    return antal


@router.patch("/work-orders/{order_id}")
async def update_order(
    order_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    o = await _hamta_order(db, order_id)
    if payload.get("status") and payload["status"] not in ORDER_STATUS:
        raise HTTPException(status_code=400, detail="Okänd status")

    tidigare = o.status

    if "customer_id" in payload:
        nytt = payload["customer_id"]
        if nytt:
            kund = (
                await db.execute(select(Customer).where(Customer.id == nytt))
            ).unique().scalar_one_or_none()
            if kund is None:
                raise HTTPException(status_code=404, detail="Kunden finns inte")
            # Byte av kund på en fakturerad order skulle flytta pengar mellan
            # kunder i efterhand, och det ska inte gå.
            if o.customer_id and o.customer_id != nytt and o.status in ("fakturerad", "betald"):
                raise HTTPException(
                    status_code=400,
                    detail="En fakturerad order kan inte flyttas till en annan kund",
                )
            o.customer_id = nytt
            if payload.get("facility_id") is None:
                o.facility_id = None
        elif o.status in ("fakturerad", "betald"):
            raise HTTPException(
                status_code=400, detail="En fakturerad order måste höra till en kund"
            )
        else:
            o.customer_id = None
            o.facility_id = None

    for falt in (
        "status", "title", "description", "performed_at", "performed_by",
        "invoiced_at", "invoice_no", "paid_at", "discount_percent", "rot_deduction",
        "facility_id",
    ):
        if falt in payload:
            setattr(o, falt, payload[falt])

    if o.status != tidigare and o.status != "oppen" and not o.customer_id:
        raise HTTPException(
            status_code=400,
            detail="Välj vilken kund ordern gäller innan den markeras som utförd.",
        )

    idag = date.today().isoformat()
    dragna = 0
    if o.status != tidigare:
        if o.status in ("utford", "fakturerad", "betald"):
            if not o.performed_at:
                o.performed_at = idag
            dragna = await _dra_lager(db, o, user)
        if o.status in ("fakturerad", "betald") and not o.invoiced_at:
            o.invoiced_at = idag
        if o.status == "betald" and not o.paid_at:
            o.paid_at = idag

    await db.commit()
    await db.refresh(o)
    await log_action(
        db, "ORDER_UPDATE", actor=user.username, object_type="work_order", object_id=o.id,
        request=request, detail=f"{o.order_no} {tidigare} -> {o.status}",
    )
    kundnamn = ""
    if o.customer_id:
        kundnamn = (
            await db.execute(select(Customer.name).where(Customer.id == o.customer_id))
        ).scalar() or ""
    svar = order_ut(o, await _rader(db, work_order_id=o.id), kundnamn)
    svar["stock_lines_deducted"] = dragna
    return svar


@router.get("/work-orders/{order_id}/pdf")
async def order_pdf(
    order_id: str,
    ladda_ner: bool = False,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    o = await _hamta_order(db, order_id)
    pdf = await _bygg_dokument(db, order=o)
    disp = "attachment" if ladda_ner else "inline"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp}; filename="{o.order_no}.pdf"'},
    )


@router.post("/work-orders/{order_id}/save-pdf")
async def save_order_pdf(
    order_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Sparar arbetsordern som PDF bland kundens dokument."""
    o = await _hamta_order(db, order_id)
    if not o.customer_id:
        raise HTTPException(
            status_code=400,
            detail="Välj vilken kund ordern gäller innan den sparas bland dokumenten.",
        )
    pdf = await _bygg_dokument(db, order=o)
    sparad = await _spara_som_dokument(
        db,
        customer_id=o.customer_id,
        filnamn=f"{o.order_no} arbetsorder.pdf",
        pdf=pdf,
        user=user,
        facility_id=o.facility_id,
        caption=o.title or "Arbetsorder",
    )
    await db.commit()
    await log_action(
        db, "ORDER_PDF_SAVED", actor=user.username, object_type="work_order",
        object_id=o.id, request=request, detail=o.order_no,
    )
    return {"ok": True, "file_id": sparad.id if sparad else None}


@router.delete("/work-orders/{order_id}", status_code=204)
async def delete_order(
    order_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    o = await _hamta_order(db, order_id)
    if o.status in ("fakturerad", "betald"):
        raise HTTPException(
            status_code=400,
            detail="En fakturerad order raderas inte. Sätt status till makulerad i stället.",
        )
    nummer = o.order_no
    await db.delete(o)
    await db.commit()
    await log_action(
        db, "ORDER_DELETE", actor=user.username, object_type="work_order",
        object_id=order_id, request=request, detail=nummer,
    )
