"""SGU:s brunnsarkiv.

Hämtar öppna data från SGU och lagrar dem lokalt, så att uppslag inför ett kundbesök
går på millisekunder utan att ringa ut varje gång. Data uppdateras hos SGU en gång i
veckan, så en synk i veckan räcker.

Källa: https://resource.sgu.se/oppnadata/grundvatten/brunnar/v1/
Licens: Creative Commons Erkännande 4.0. SGU ska anges som källa där uppgifterna visas.

Koordinaterna kommer i SWEREF 99 TM, samma system som appens egen omvandling hanterar.
Avstånd räknas planärt i meter, vilket är exakt nog på de avstånd det handlar om.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import SguWell
from ..schemas import iso_utc
from .geo import sweref99tm_to_wgs84, wgs84_to_sweref99tm

# Brunnstyper enligt SGU:s kodlista
ANVANDNING = {
    "HUS": "Hushåll eller fritidshus",
    "ENE": "Energibrunn",
    "LAN": "Lantbruk",
    "BEV": "Bevattning",
    "IND": "Industri",
    "OBS": "Observationsbrunn",
    "SAM": "Samfälld vattentäkt",
    "VAF": "Vattenförening",
    "ÖVR": "Annan användning",
}
VATTENBRUNNAR = {"HUS", "LAN", "BEV", "IND", "SAM", "VAF", "ÖVR"}

LAN_NAMN = {
    "01": "Stockholm", "03": "Uppsala", "04": "Södermanland", "05": "Östergötland",
    "06": "Jönköping", "07": "Kronoberg", "08": "Kalmar", "09": "Gotland",
    "10": "Blekinge", "12": "Skåne", "13": "Halland", "14": "Västra Götaland",
    "17": "Värmland", "18": "Örebro", "19": "Västmanland", "20": "Dalarna",
    "21": "Gävleborg", "22": "Västernorrland", "23": "Jämtland",
    "24": "Västerbotten", "25": "Norrbotten",
}


def _tal(v) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _text(v) -> str:
    return "" if v is None else str(v).strip()


async def sync_lan(db: AsyncSession, lanskod: str, progress=None) -> dict:
    """Hämtar samtliga brunnar i ett län och ersätter det som fanns sedan tidigare."""
    import httpx

    bas = settings.sgu_base_url.rstrip("/")
    url = f"{bas}/lan/{lanskod}"
    hamtade: list[dict] = []
    sida = 1

    async with httpx.AsyncClient(timeout=120.0) as client:
        while True:
            r = await client.get(
                url,
                params={"format": "json", "limit": 10000, "page": sida},
                headers={"User-Agent": settings.geocoder_user_agent},
            )
            r.raise_for_status()
            data = r.json()
            poster = data.get("brunnar") if isinstance(data, dict) else data
            if isinstance(data, dict) and poster is None:
                # Fältnamnet kan variera, ta första listan som finns
                poster = next((v for v in data.values() if isinstance(v, list)), [])
            if not poster:
                break
            hamtade.extend(poster)
            if progress:
                progress(len(hamtade))
            if len(poster) < 10000:
                break
            sida += 1
            if sida > 60:  # skyddsnät mot oändlig loop
                break

    rader = []
    for p in hamtade:
        n = _tal(p.get("n"))
        e = _tal(p.get("e"))
        if n is None or e is None:
            continue
        lat, lon = sweref99tm_to_wgs84(n, e)
        rader.append(
            {
                "brunnsid": _text(p.get("brunnsid")),
                "lanskod": lanskod,
                "kommunkod": _text(p.get("kommunkod")),
                "n": n,
                "e": e,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "lagesnoggrannhet": _text(p.get("lagesnoggrannhet")),
                "fastighet": _text(p.get("fastighet"))[:120],
                "ort": _text(p.get("ort"))[:80],
                "borrdatum": _text(p.get("borrdatum"))[:10],
                "totaldjup": _tal(p.get("totaldjup")),
                "djup_till_berg": _tal(p.get("djupTillBerg")),
                "vattenmangd": _tal(p.get("vattenmangd")),
                "grundvattenniva": _tal(p.get("grundvattenniva")),
                "foderror_till": _tal(p.get("stalfoderrorTill")) or _tal(p.get("rorborrningTill")),
                "anvandning": _text(p.get("anvandning"))[:10].upper(),
                "tatning": _text(p.get("tatning"))[:10],
                "anmarkning": _text(p.get("anmarkning"))[:255],
            }
        )

    await db.execute(delete(SguWell).where(SguWell.lanskod == lanskod))
    for i in range(0, len(rader), 1000):
        db.add_all(SguWell(**rad) for rad in rader[i : i + 1000])
        await db.flush()
    await db.commit()
    return {"lanskod": lanskod, "hamtade": len(hamtade), "sparade": len(rader)}


async def neighbours(
    db: AsyncSession, lat: float, lon: float, radius_m: float = 1000, limit: int = 60
) -> list[dict]:
    """Brunnar inom radien, närmast först. Grovsållar på ruta, mäter sedan exakt."""
    n0, e0 = wgs84_to_sweref99tm(lat, lon)
    rows = (
        await db.execute(
            select(SguWell).where(
                SguWell.n.between(n0 - radius_m, n0 + radius_m),
                SguWell.e.between(e0 - radius_m, e0 + radius_m),
            )
        )
    ).scalars().all()

    traffar = []
    for w in rows:
        avstand = math.hypot(w.n - n0, w.e - e0)
        if avstand > radius_m:
            continue
        traffar.append(
            {
                "brunnsid": w.brunnsid,
                "avstand_m": round(avstand),
                "latitude": w.latitude,
                "longitude": w.longitude,
                "fastighet": w.fastighet,
                "ort": w.ort,
                "borrdatum": w.borrdatum,
                "totaldjup": w.totaldjup,
                "djup_till_berg": w.djup_till_berg,
                "vattenmangd": w.vattenmangd,
                "grundvattenniva": w.grundvattenniva,
                "foderror_till": w.foderror_till,
                "anvandning": w.anvandning,
                "anvandning_text": ANVANDNING.get(w.anvandning, w.anvandning or "okänd"),
                "lagesnoggrannhet": w.lagesnoggrannhet,
            }
        )
    traffar.sort(key=lambda x: x["avstand_m"])
    return traffar[:limit]


def _sammanfatta(varden: list[float]) -> dict | None:
    rena = [v for v in varden if v is not None and v > 0]
    if not rena:
        return None
    return {
        "antal": len(rena),
        "min": round(min(rena), 1),
        "median": round(statistics.median(rena), 1),
        "max": round(max(rena), 1),
    }


async def briefing(
    db: AsyncSession, lat: float, lon: float, radius_m: float = 1000
) -> dict:
    """Underlaget inför ett besök: vad grannarna stötte på och vad de fick."""
    alla = await neighbours(db, lat, lon, radius_m)
    vatten = [w for w in alla if w["anvandning"] in VATTENBRUNNAR]
    energi = [w for w in alla if w["anvandning"] == "ENE"]

    berg = _sammanfatta([w["djup_till_berg"] for w in alla])
    djup_vatten = _sammanfatta([w["totaldjup"] for w in vatten])
    djup_energi = _sammanfatta([w["totaldjup"] for w in energi])
    kapacitet = _sammanfatta([w["vattenmangd"] for w in vatten])
    niva = _sammanfatta([w["grundvattenniva"] for w in alla])

    kapaciteter = [w["vattenmangd"] for w in vatten if w["vattenmangd"]]
    svaga = [v for v in kapaciteter if v < 600]

    return {
        "radius_m": radius_m,
        "antal": len(alla),
        "antal_vattenbrunnar": len(vatten),
        "antal_energibrunnar": len(energi),
        "jorddjup": berg,
        "borrdjup_vatten": djup_vatten,
        "borrdjup_energi": djup_energi,
        "kapacitet": kapacitet,
        "grundvattenniva": niva,
        "svag_kapacitet_andel": round(100 * len(svaga) / len(kapaciteter)) if kapaciteter else None,
        "narmaste": alla[:12],
        "kalla": "SGU Brunnsarkivet, CC BY 4.0",
        "vattenkvalitet": (
            "Brunnsarkivet innehåller ingen vattenkvalitet. Kapacitet och djup finns, "
            "men kemi och bakterier går bara att få genom ett vattenprov på plats."
        ),
    }


async def status(db: AsyncSession) -> dict:
    from sqlalchemy import func

    rows = (
        await db.execute(
            select(SguWell.lanskod, func.count(SguWell.brunnsid), func.max(SguWell.hamtad_at))
            .group_by(SguWell.lanskod)
        )
    ).all()
    return {
        "lan": [
            {
                "lanskod": r[0],
                "namn": LAN_NAMN.get(r[0], r[0]),
                "antal": r[1],
                "hamtad": iso_utc(r[2]),
            }
            for r in rows
        ],
        "totalt": sum(r[1] for r in rows),
        "tillgangliga_lan": [{"kod": k, "namn": v} for k, v in sorted(LAN_NAMN.items())],
        "senast": max((iso_utc(r[2]) for r in rows if r[2]), default=None),
    }


def is_stale(hamtad: datetime | None, dagar: int = 7) -> bool:
    if hamtad is None:
        return True
    if hamtad.tzinfo is None:
        hamtad = hamtad.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - hamtad).days >= dagar
