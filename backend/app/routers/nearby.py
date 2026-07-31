"""Jobb i närheten.

Två användningsfall:
  1. Montören står någonstans och undrar vad mer som finns runt omkring.
  2. Montören ska åka till en kund och vill veta vad som kan slås ihop med resan.

Registret rymmer några tusen anläggningar i värsta fall, så avstånden räknas i Python.
Det slipper databasspecifika geo-tillägg och fungerar likadant på SQLite och Postgres.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Customer, Facility, Reminder, User
from ..schemas import derived_status, service_due
from ..security import current_user
from ..services.geo import bearing_label, haversine_km, parse_coordinates

router = APIRouter(prefix="/api", tags=["narhet"])


async def _candidates(db: AsyncSession) -> list[Facility]:
    return (
        (await db.execute(select(Facility).join(Customer).where(Facility.latitude.isnot(None))))
        .unique()
        .scalars()
        .all()
    )


async def _open_reminders(db: AsyncSession) -> dict[str, list[Reminder]]:
    rows = (await db.execute(select(Reminder).where(Reminder.status == "open"))).scalars().all()
    grouped: dict[str, list[Reminder]] = {}
    for r in rows:
        if r.facility_id:
            grouped.setdefault(r.facility_id, []).append(r)
        if r.customer_id:
            grouped.setdefault(f"c:{r.customer_id}", []).append(r)
    return grouped


def _reason(facility: Facility, reminders: list[Reminder]) -> tuple[str, int]:
    """Varför den här dyker upp, och hur angeläget det är. Högre tal = viktigare."""
    today = date.today().isoformat()
    overdue = [r for r in reminders if r.due_date < today]
    if overdue:
        return f"{len(overdue)} försenad påminnelse" + ("r" if len(overdue) > 1 else ""), 3
    status = derived_status(facility)
    if status == "action":
        return "Åtgärd krävs", 3
    soon = sorted(reminders, key=lambda r: r.due_date)
    if soon:
        return f"Påminnelse {soon[0].due_date}", 2
    if status == "soon":
        due = service_due(facility)
        return f"Service senast {due}" if due else "Service snart", 2
    return "Inget öppet just nu", 0


def _serialise(f: Facility, distance: float, bearing: str, reminders: list[Reminder]) -> dict:
    reason, priority = _reason(f, reminders)
    return {
        "facility_id": f.id,
        "facility_no": f.facility_no,
        "facility_type": f.facility_type,
        "customer_id": f.customer_id,
        "customer_name": f.customer.name if f.customer else "",
        "phone": f.customer.phone if f.customer else "",
        "property_designation": f.customer.property_designation if f.customer else "",
        "municipality": f.customer.municipality if f.customer else "",
        "latitude": f.latitude,
        "longitude": f.longitude,
        "distance_km": round(distance, 2),
        "bearing": bearing,
        "status": derived_status(f),
        "service_due": service_due(f),
        "open_reminders": len(reminders),
        "reason": reason,
        "priority": priority,
    }


async def _search(
    db: AsyncSession,
    lat: float,
    lon: float,
    radius_km: float,
    only_jobs: bool,
    limit: int,
    exclude_facility: str | None = None,
) -> list[dict]:
    facilities = await _candidates(db)
    reminders = await _open_reminders(db)

    hits = []
    for f in facilities:
        if exclude_facility and f.id == exclude_facility:
            continue
        distance = haversine_km(lat, lon, f.latitude, f.longitude)
        if distance > radius_km:
            continue
        rows = reminders.get(f.id, []) + [
            r for r in reminders.get(f"c:{f.customer_id}", []) if not r.facility_id
        ]
        item = _serialise(f, distance, bearing_label(lat, lon, f.latitude, f.longitude), rows)
        if only_jobs and item["priority"] == 0:
            continue
        hits.append(item)

    # Angelägenhet först, därefter närhet. En försenad service 20 km bort är mer
    # värd en avstickare än en fungerande brunn på samma gata.
    hits.sort(key=lambda h: (-h["priority"], h["distance_km"]))
    return hits[:limit]


@router.get("/nearby")
async def nearby(
    lat: float | None = None,
    lon: float | None = None,
    q: str | None = Query(None, description="Koordinat som text, t.ex. SWEREF 99 TM"),
    radius_km: float = 25,
    only_jobs: bool = False,
    limit: int = 40,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if lat is None or lon is None:
        parsed = parse_coordinates(q or "")
        if not parsed:
            raise HTTPException(
                status_code=400, detail="Ange lat och lon, eller en koordinat som går att tolka"
            )
        lat, lon = parsed

    hits = await _search(db, lat, lon, radius_km, only_jobs, limit)
    total = len(await _candidates(db))
    return {
        "origin": {"latitude": lat, "longitude": lon},
        "radius_km": radius_km,
        "results": hits,
        "with_coordinates": total,
    }


@router.get("/facilities/{facility_id}/nearby")
async def nearby_facility(
    facility_id: str,
    radius_km: float = 25,
    only_jobs: bool = True,
    limit: int = 20,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vad kan slås ihop med resan till den här anläggningen?"""
    f = (
        await db.execute(select(Facility).where(Facility.id == facility_id))
    ).unique().scalar_one_or_none()
    if f is None:
        raise HTTPException(status_code=404, detail="Anläggningen finns inte")
    if f.latitude is None or f.longitude is None:
        return {
            "origin": None,
            "missing_coordinates": True,
            "results": [],
            "hint": "Anläggningen saknar koordinater. Fyll i dem så kan resan planeras härifrån.",
        }

    hits = await _search(
        db, f.latitude, f.longitude, radius_km, only_jobs, limit, exclude_facility=f.id
    )
    return {
        "origin": {
            "facility_no": f.facility_no,
            "latitude": f.latitude,
            "longitude": f.longitude,
        },
        "radius_km": radius_km,
        "results": hits,
    }


@router.get("/geocode")
async def geocode_address(
    q: str,
    municipality: str = "",
    _: User = Depends(current_user),
):
    """Slår upp koordinater från en adress eller fastighetsbeteckning."""
    from ..config import settings
    from ..services.geocode import geocode

    if not settings.geocoder_url:
        raise HTTPException(status_code=503, detail="Adressuppslag är avstängt på servern")
    try:
        hit = await geocode(q, municipality)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not hit:
        raise HTTPException(
            status_code=404,
            detail="Hittade ingen träff på adressen. Skriv koordinaten för hand.",
        )
    return hit


@router.get("/coordinates/parse")
async def parse_endpoint(q: str, _: User = Depends(current_user)):
    """Låter gränssnittet visa tolkningen direkt medan man skriver."""
    parsed = parse_coordinates(q)
    if not parsed:
        return {"ok": False, "detail": "Kunde inte tolka koordinaten"}
    return {"ok": True, "latitude": parsed[0], "longitude": parsed[1]}
