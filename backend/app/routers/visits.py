from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, Facility, JournalEntry, ShareLog, User, Visit
from ..schemas import customer_out, facility_out, iso_utc
from ..security import current_user, log_action, require_admin, require_write
from ..services import sgu
from ..services.backgrund import slag_upp_adress
from ..services.geo import parse_coordinates
from ..services.numrering import nasta_nummer, nummerlas, spara_numrerad, nummerlas_beroende

router = APIRouter(prefix="/api", tags=["besok"])

STATUSAR = ["planerat", "genomfort", "offert", "vunnen", "forlorad"]
STATUS_TEXT = {
    "planerat": "Inbokat",
    "genomfort": "Besökt",
    "offert": "Offert lämnad",
    "vunnen": "Blev kund",
    "forlorad": "Blev inget",
}


class VisitIn(BaseModel):
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    property_designation: str = ""
    address: str = ""
    municipality: str = ""
    coordinates: str = ""
    latitude: float | None = None
    longitude: float | None = None
    errand: str = ""
    notes: str = ""
    planned_at: str = ""
    status: str = "planerat"
    quote_amount: float | None = None
    quote_sent_at: str = ""
    lost_reason: str = ""


def visit_out(v: Visit) -> dict:
    return {
        "id": v.id,
        "visit_no": v.visit_no,
        "status": v.status,
        "status_text": STATUS_TEXT.get(v.status, v.status),
        "planned_at": v.planned_at,
        "contact_name": v.contact_name,
        "phone": v.phone,
        "email": v.email,
        "property_designation": v.property_designation,
        "address": v.address,
        "municipality": v.municipality,
        "coordinates": v.coordinates,
        "latitude": v.latitude,
        "longitude": v.longitude,
        "geocode_status": v.geocode_status,
        "geocode_message": v.geocode_message,
        "errand": v.errand,
        "notes": v.notes,
        "quote_amount": v.quote_amount,
        "quote_sent_at": v.quote_sent_at,
        "lost_reason": v.lost_reason,
        "customer_id": v.customer_id,
        "created_at": iso_utc(v.created_at) if v.created_at else None,
        "created_by": v.created_by,
    }


async def get_visit(db: AsyncSession, visit_id: str) -> Visit:
    v = (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one_or_none()
    if v is None:
        raise HTTPException(status_code=404, detail="Besöket finns inte")
    return v


def apply_coords(data: dict) -> dict:
    if data.get("latitude") is None or data.get("longitude") is None:
        hit = parse_coordinates(data.get("coordinates", "") or "")
        if hit:
            data["latitude"], data["longitude"] = hit
    return data


def behover_uppslag(obj) -> bool:
    """Sant om posten saknar koordinat men har en adress att slå upp."""
    if obj.latitude is not None and obj.longitude is not None:
        return False
    return bool((obj.address or "").strip() or (obj.property_designation or "").strip())


# ---------- besök ----------
@router.get("/visits")
async def list_visits(
    status: str | None = None,
    q: str | None = None,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Visit).order_by(Visit.planned_at.desc(), Visit.created_at.desc())
    if status == "aktiva":
        stmt = stmt.where(Visit.status.in_(["planerat", "genomfort", "offert"]))
    elif status:
        stmt = stmt.where(Visit.status == status)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Visit.contact_name).like(needle),
                func.lower(Visit.property_designation).like(needle),
                func.lower(Visit.address).like(needle),
                func.lower(Visit.municipality).like(needle),
                func.lower(Visit.visit_no).like(needle),
            )
        )
    return [visit_out(v) for v in (await db.execute(stmt)).scalars().all()]


@router.post("/visits", status_code=201)
async def create_visit(
    payload: VisitIn,
    request: Request,
    bakgrund: BackgroundTasks,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    if not (payload.property_designation or payload.address or payload.contact_name):
        raise HTTPException(
            status_code=400, detail="Ange åtminstone fastighet, adress eller kontaktperson"
        )
    v = Visit(
        created_by=user.full_name or user.username,
        **apply_coords(payload.model_dump()),
    )
    # Sparas direkt. Adressuppslaget sker efteråt, så att en trög karttjänst
    # aldrig kan hindra eller fördröja att besöket bokas in.
    if behover_uppslag(v):
        v.geocode_status = "pagar"
    async with nummerlas():
        await spara_numrerad(db, v, Visit, Visit.visit_no, "BES")
    await db.refresh(v)
    if v.geocode_status == "pagar":
        bakgrund.add_task(slag_upp_adress, "visit", v.id)
    await log_action(
        db, "VISIT_CREATE", actor=user.username, object_type="visit", object_id=v.id, request=request
    )
    return visit_out(v)


@router.get("/visits/{visit_id}")
async def read_visit(
    visit_id: str, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    return visit_out(await get_visit(db, visit_id))


@router.patch("/visits/{visit_id}")
async def update_visit(
    visit_id: str,
    payload: dict,
    request: Request,
    bakgrund: BackgroundTasks,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    v = await get_visit(db, visit_id)
    if payload.get("status") and payload["status"] not in STATUSAR:
        raise HTTPException(status_code=400, detail="Okänd status")
    if "coordinates" in payload and "latitude" not in payload:
        payload = apply_coords({**payload, "latitude": None, "longitude": None})
    adress_fore = (v.address, v.property_designation, v.municipality)
    for f in VisitIn.model_fields:
        if f in payload:
            setattr(v, f, payload[f])

    # Ändrad adress utan att koordinaten rörts: slå upp den nya platsen
    adress_andrad = (v.address, v.property_designation, v.municipality) != adress_fore
    if adress_andrad and "coordinates" not in payload and "latitude" not in payload:
        v.latitude = None
        v.longitude = None
        v.coordinates = ""
        v.geocode_message = ""
    slag_upp = behover_uppslag(v)
    if slag_upp:
        v.geocode_status = "pagar"
    elif v.latitude is not None:
        v.geocode_status = "klar"

    await db.commit()
    await db.refresh(v)
    if slag_upp:
        bakgrund.add_task(slag_upp_adress, "visit", v.id)
    await log_action(
        db, "VISIT_UPDATE", actor=user.username, object_type="visit", object_id=v.id, request=request
    )
    return visit_out(v)


@router.delete("/visits/{visit_id}", status_code=204)
async def delete_visit(
    visit_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    v = await get_visit(db, visit_id)
    label = f"{v.visit_no} {v.contact_name or v.property_designation}"
    await db.delete(v)
    await db.commit()
    await log_action(
        db, "VISIT_DELETE", actor=user.username, object_type="visit", object_id=visit_id,
        request=request, detail=label,
    )


@router.post("/visits/{visit_id}/convert", status_code=201)
async def convert_visit(
    visit_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    """Besöket blev affär: skapa kund och första anläggningen av det som redan matats in."""
    v = await get_visit(db, visit_id)
    if v.customer_id:
        raise HTTPException(status_code=409, detail="Besöket är redan omvandlat till kund")

    from .customers import next_no

    customer = Customer(
        customer_no=await next_no(db, Customer, Customer.customer_no, "K", 1000),
        name=payload.get("name") or v.contact_name or v.property_designation or "Ny kund",
        customer_type=payload.get("customer_type", "Privat"),
        phone=v.phone,
        email=v.email,
        property_designation=v.property_designation,
        address=v.address,
        municipality=v.municipality,
        notes=v.notes,
    )
    db.add(customer)
    await db.flush()

    facility = None
    if payload.get("create_facility", True):
        facility = Facility(
            facility_no=await next_no(db, Facility, Facility.facility_no, "B", 2000),
            customer_id=customer.id,
            facility_type=payload.get("facility_type", "Bergborrad brunn"),
            coordinates=v.coordinates,
            latitude=v.latitude,
            longitude=v.longitude,
            access_notes=v.notes,
            pump_status="Ska installeras",
        )
        db.add(facility)
        await db.flush()

    db.add(
        JournalEntry(
            customer_id=customer.id,
            facility_id=facility.id if facility else None,
            entry_type="Registrering",
            title=f"Kund skapad från besök {v.visit_no}",
            body=(f"Ärende vid besöket: {v.errand}\n" if v.errand else "")
            + (f"Anteckningar: {v.notes}\n" if v.notes else "")
            + (f"Offert: {v.quote_amount:.0f} kr\n" if v.quote_amount else ""),
            author_id=user.id,
            author_name=user.full_name or user.username,
        )
    )

    # Offerter som lagts på besöket ska följa med till kunden, annars tappas de
    from ..models import Quote

    offerter = (
        await db.execute(select(Quote).where(Quote.visit_id == v.id))
    ).scalars().all()
    for q in offerter:
        q.customer_id = customer.id
        if facility is not None and not q.facility_id:
            q.facility_id = facility.id

    v.customer_id = customer.id
    v.status = "vunnen"
    await db.commit()
    await db.refresh(customer)
    await log_action(
        db, "VISIT_CONVERT", actor=user.username, object_type="visit", object_id=v.id,
        request=request, detail=f"{v.visit_no} -> {customer.customer_no}",
    )
    return {
        "customer": customer_out(customer),
        "facility": facility_out(facility) if facility else None,
        "visit": visit_out(v),
        "quotes_moved": len(offerter),
    }


# ---------- SGU ----------
SGU_INSTALLNING = {"lan": [], "auto": True, "dagar": 7}


@router.get("/sgu/status")
async def sgu_status(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from ..services.notify import get_setting

    data = await sgu.status(db)
    data["installning"] = await get_setting(db, "sgu", SGU_INSTALLNING)
    return data


@router.put("/sgu/settings")
async def sgu_settings(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Vilka län som ska hållas uppdaterade automatiskt."""
    from ..services.notify import get_setting, save_setting

    conf = await get_setting(db, "sgu", SGU_INSTALLNING)
    if "lan" in payload:
        valda = [str(k).zfill(2) for k in payload["lan"] if str(k).zfill(2) in sgu.LAN_NAMN]
        conf["lan"] = sorted(set(valda))
    if "auto" in payload:
        conf["auto"] = bool(payload["auto"])
    if "dagar" in payload:
        conf["dagar"] = max(1, min(90, int(payload["dagar"])))
    await save_setting(db, "sgu", conf)
    await log_action(
        db,
        "SGU_SETTINGS",
        actor=user.username,
        request=request,
        detail=f"län: {', '.join(conf['lan']) or 'inga'}, auto: {conf['auto']}",
    )
    return conf


@router.post("/sgu/sync")
async def sgu_sync(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Hämtar ett län. Tar en stund första gången, sedan räcker en gång i veckan."""
    lanskod = str(payload.get("lanskod", "")).zfill(2)
    if lanskod not in sgu.LAN_NAMN:
        raise HTTPException(status_code=400, detail="Okänd länskod")
    try:
        resultat = await sgu.sync_lan(db, lanskod)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Kunde inte hämta från SGU: {exc}. Kontrollera att servern når internet.",
        ) from exc
    await log_action(
        db, "SGU_SYNC", actor=user.username, request=request,
        detail=f"{sgu.LAN_NAMN[lanskod]}: {resultat['sparade']} brunnar",
    )
    return resultat


@router.get("/sgu/briefing")
async def sgu_briefing(
    lat: float | None = None,
    lon: float | None = None,
    visit_id: str | None = None,
    facility_id: str | None = None,
    radius_m: float = 1000,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vad grannarna stötte på. Underlaget man vill ha i handen på plats."""
    if visit_id:
        v = await get_visit(db, visit_id)
        lat, lon = v.latitude, v.longitude
    elif facility_id:
        f = (
            await db.execute(select(Facility).where(Facility.id == facility_id))
        ).unique().scalar_one_or_none()
        if f is None:
            raise HTTPException(status_code=404, detail="Anläggningen finns inte")
        lat, lon = f.latitude, f.longitude

    if lat is None or lon is None:
        raise HTTPException(
            status_code=400,
            detail="Saknar koordinat. Fyll i den eller hämta från adressen först.",
        )
    return await sgu.briefing(db, lat, lon, min(radius_m, 5000))


# ---------- dela med extern borrare ----------
FALT = {
    "plats": "Fastighet, adress och koordinat",
    "kontakt": "Namn och telefon till kontaktpersonen",
    "arende": "Vad ärendet gäller",
    "atkomst": "Åtkomst och förutsättningar",
    "borrning": "Borrdata: djup, jorddjup, foderrör, kapacitet",
    "berg": "Bergarter och lagerföljd",
    "pump": "Pumpuppgifter",
    "grannar": "Underlag från SGU om grannbrunnar",
}

# Ett platsbesök har ingen borrning gjord än, så de fälten erbjuds inte.
# Att visa dem hade gett kryssrutor som tyst inte producerar någonting.
FALT_BESOK = ["plats", "kontakt", "arende", "atkomst", "grannar"]
FALT_ANLAGGNING = ["plats", "kontakt", "atkomst", "borrning", "berg", "pump", "grannar"]
FORVALDA_BESOK = ["plats", "arende", "atkomst"]
FORVALDA_ANLAGGNING = ["plats", "atkomst", "borrning"]


@router.get("/share/fields")
async def share_fields(
    visit_id: str | None = None,
    facility_id: str | None = None,
    _: User = Depends(current_user),
):
    """Vilka fält som går att dela beror på om det är ett besök eller en anläggning."""
    if visit_id:
        nycklar, forvalda = FALT_BESOK, FORVALDA_BESOK
        etiketter = {**FALT, "atkomst": "Anteckningar från platsen"}
    else:
        nycklar, forvalda = FALT_ANLAGGNING, FORVALDA_ANLAGGNING
        etiketter = FALT
    return {
        "fields": [
            {"key": k, "label": etiketter[k], "default": k in forvalda} for k in nycklar
        ],
        "kind": "visit" if visit_id else "facility",
    }


@router.post("/share")
async def share(
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Skickar valda uppgifter till en extern borrare med e-post.

    Bara utgående. Ingenting öppnas mot internet, och bara det som kryssas i följer med.
    """
    from ..services.notify import DEFAULT_SMTP, SMTP_KEY, get_setting, send_email

    mottagare = (payload.get("recipient") or "").strip()
    if "@" not in mottagare:
        raise HTTPException(status_code=400, detail="Ange en giltig e-postadress")
    tillatna = FALT_BESOK if payload.get("visit_id") else FALT_ANLAGGNING
    onskade = payload.get("fields", [])
    valda = [f for f in onskade if f in tillatna]
    if not valda:
        raise HTTPException(status_code=400, detail="Välj minst en uppgift att dela")
    ogiltiga = [f for f in onskade if f in FALT and f not in tillatna]
    if ogiltiga:
        raise HTTPException(
            status_code=400,
            detail="Ett platsbesök har inga borrdata att dela: "
            + ", ".join(FALT[f].split(":")[0].lower() for f in ogiltiga),
        )

    facility = None
    visit = None
    rubrik = ""
    rader: list[str] = []

    if payload.get("facility_id"):
        facility = (
            await db.execute(select(Facility).where(Facility.id == payload["facility_id"]))
        ).unique().scalar_one_or_none()
        if facility is None:
            raise HTTPException(status_code=404, detail="Anläggningen finns inte")
        c = facility.customer
        rubrik = f"{facility.facility_no}, {c.property_designation or c.name}"
        if "plats" in valda:
            rader += [
                "PLATS",
                f"  Fastighet: {c.property_designation or '—'}",
                f"  Adress: {c.address or '—'}, {c.municipality or ''}".rstrip(", "),
                f"  Koordinat: {facility.coordinates or (f'{facility.latitude}, {facility.longitude}' if facility.latitude else '—')}",
                "",
            ]
        if "kontakt" in valda:
            rader += ["KONTAKT", f"  {c.name}", f"  {c.phone or '—'}", ""]
        if "atkomst" in valda and facility.access_notes:
            rader += ["ÅTKOMST", f"  {facility.access_notes}", ""]
        if "borrning" in valda:
            rader += [
                "BORRNING",
                f"  Typ: {facility.facility_type}",
                f"  Totalt djup: {facility.total_depth_m or '—'} m",
                f"  Jorddjup: {facility.soil_depth_m or '—'} m",
                f"  Foderrör: {facility.casing_length_m or '—'} m",
                f"  Vattennivå: {facility.water_level_m or '—'} m",
                f"  Kapacitet: {facility.capacity_lph or '—'} l/h",
                "",
            ]
        if "berg" in valda and facility.bedrock_notes:
            rader += ["BERG OCH LAGER", f"  {facility.bedrock_notes}", ""]
        if "pump" in valda:
            rader += [
                "PUMP",
                f"  {facility.pump_manufacturer} {facility.pump_model}".strip() or "  —",
                f"  Serienummer: {facility.pump_serial or '—'}",
                f"  Pumpdjup: {facility.pump_depth_m or '—'} m",
                "",
            ]
        lat, lon = facility.latitude, facility.longitude

    elif payload.get("visit_id"):
        visit = await get_visit(db, payload["visit_id"])
        rubrik = f"{visit.visit_no}, {visit.property_designation or visit.address or 'platsbesök'}"
        if "plats" in valda:
            rader += [
                "PLATS",
                f"  Fastighet: {visit.property_designation or '—'}",
                f"  Adress: {visit.address or '—'}, {visit.municipality or ''}".rstrip(", "),
                f"  Koordinat: {visit.coordinates or '—'}",
                *( [f"  Planerat besök: {visit.planned_at}"] if visit.planned_at else [] ),
                "",
            ]
        if "kontakt" in valda:
            rader += ["KONTAKT", f"  {visit.contact_name or '—'}", f"  {visit.phone or '—'}", ""]
        if "arende" in valda and visit.errand:
            rader += ["ÄRENDE", f"  {visit.errand}", ""]
        if "atkomst" in valda and visit.notes:
            rader += ["ANTECKNINGAR FRÅN PLATSEN", f"  {visit.notes}", ""]
        lat, lon = visit.latitude, visit.longitude
    else:
        raise HTTPException(status_code=400, detail="Ange anläggning eller besök att dela")

    if "grannar" in valda and lat and lon:
        b = await sgu.briefing(db, lat, lon)
        if b["antal"]:
            rader += ["GRANNBRUNNAR ENLIGT SGU (inom 1 km)", f"  Antal: {b['antal']}"]
            if b["jorddjup"]:
                rader.append(
                    f"  Jorddjup: {b['jorddjup']['min']}–{b['jorddjup']['max']} m, "
                    f"median {b['jorddjup']['median']} m"
                )
            if b["borrdjup_vatten"]:
                rader.append(
                    f"  Borrdjup vattenbrunnar: {b['borrdjup_vatten']['min']}–"
                    f"{b['borrdjup_vatten']['max']} m, median {b['borrdjup_vatten']['median']} m"
                )
            if b["kapacitet"]:
                rader.append(
                    f"  Kapacitet: {b['kapacitet']['min']}–{b['kapacitet']['max']} l/h, "
                    f"median {b['kapacitet']['median']} l/h"
                )
            rader += ["  Källa: SGU Brunnsarkivet, CC BY 4.0", ""]

    meddelande = (payload.get("message") or "").strip()
    body = (
        (meddelande + "\n\n" if meddelande else "")
        + "\n".join(rader)
        + f"\nSkickat av {user.full_name or user.username} via Borrjournal {date.today().isoformat()}.\n"
        + "Uppgifterna delas för det aktuella uppdraget och ska inte spridas vidare.\n"
    )
    amne = payload.get("subject") or f"Uppgifter: {rubrik}"

    smtp = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    try:
        await send_email({**smtp, "enabled": True}, amne, body, [mottagare])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Kunde inte skicka: {exc}. Kontrollera e-postinställningarna.",
        ) from exc

    db.add(
        ShareLog(
            facility_id=facility.id if facility else None,
            visit_id=visit.id if visit else None,
            recipient=mottagare,
            subject=amne,
            fields=", ".join(valda),
            sent_by=user.full_name or user.username,
        )
    )
    if facility is not None:
        db.add(
            JournalEntry(
                customer_id=facility.customer_id,
                facility_id=facility.id,
                entry_type="Delning",
                title=f"Uppgifter delade med {mottagare}",
                body="Delade fält: " + ", ".join(FALT[f] for f in valda),
                author_id=user.id,
                author_name=user.full_name or user.username,
            )
        )
    await db.commit()
    await log_action(
        db, "SHARE_SENT", actor=user.username, request=request,
        detail=f"{rubrik} till {mottagare}",
    )
    return {"ok": True, "recipient": mottagare, "fields": valda}


@router.get("/share/log")
async def share_log(
    limit: int = 50, _: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(select(ShareLog).order_by(ShareLog.sent_at.desc()).limit(limit))
    ).scalars().all()
    return [
        {
            "id": r.id,
            "recipient": r.recipient,
            "subject": r.subject,
            "fields": r.fields,
            "sent_at": iso_utc(r.sent_at) if r.sent_at else None,
            "sent_by": r.sent_by,
            "facility_id": r.facility_id,
            "visit_id": r.visit_id,
        }
        for r in rows
    ]
