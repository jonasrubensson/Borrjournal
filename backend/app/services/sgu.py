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


# Kolumnnamn i SGU:s bulkfiler. Läses efter namn, inte position, så en ändrad
# kolumnordning hos SGU inte tyst förskjuter alla värden.
KOLUMNER = {
    "brunnsid": ("BRUNNS_ID",),
    "n": ("N",),
    "e": ("E",),
    "lagesnoggrannhet": ("LAGESNOGGRANNHET",),
    "kommunkod": ("KOMMUNKOD",),
    "fastighet": ("FASTIGHETSBETECKNING",),
    "ort": ("ORT",),
    "borrdatum": ("BORRDATUM",),
    "vattenmangd": ("VATTENMANGD",),
    "grundvattenniva": ("GRUNDVATTENNIVA",),
    "totaldjup": ("TOTALDJUP",),
    "djup_till_berg": ("DJUP_TILL_BERG",),
    # SGU stavar den här med ett R i bulkfilen, håll båda
    "foderror_till": ("STALFODERROR_TILL", "RORBORRNING_TILL", "PLASTFODEROR_TILL"),
    "tatning": ("TATNING",),
    "anvandning": ("ANVANDNING",),
    "anmarkning": ("ANMARKNING",),
}


def normalisera_datum(varde: str) -> str:
    """SGU skriver datum som 20120427, 199307 eller bara 1963."""
    siffror = "".join(c for c in varde if c.isdigit())
    if len(siffror) >= 8:
        return f"{siffror[:4]}-{siffror[4:6]}-{siffror[6:8]}"
    if len(siffror) == 6:
        return f"{siffror[:4]}-{siffror[4:6]}"
    if len(siffror) == 4:
        return siffror
    return ""


async def sync_lan(db: AsyncSession, lanskod: str, progress=None) -> dict:
    """Hämtar hela länets bulkfil och ersätter det som fanns sedan tidigare.

    Bulkfilen används i stället för det paginerade JSON-API:et: en enda begäran,
    ingen paginering som kan tappa sidor, och formatet är verifierat mot skarp data.
    Filerna är teckenkodade i cp1252, inte UTF-8.
    """
    import csv
    import io

    import httpx

    from sqlalchemy import insert

    url = f"{settings.sgu_bulk_url.rstrip('/')}/brunnar_lan{lanskod}.csv"
    started = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0), follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": settings.geocoder_user_agent})
        if r.status_code == 404:
            raise RuntimeError(f"SGU har ingen fil för län {lanskod} ({url})")
        r.raise_for_status()
        text = r.content.decode("cp1252", errors="replace")

    lasare = csv.reader(io.StringIO(text), delimiter=";")
    try:
        rubriker = next(lasare)
    except StopIteration:
        raise RuntimeError("Filen från SGU var tom") from None

    index = {namn.strip().upper(): i for i, namn in enumerate(rubriker)}

    def plocka(rad: list[str], falt: str) -> str:
        for namn in KOLUMNER[falt]:
            i = index.get(namn)
            if i is not None and i < len(rad) and rad[i].strip():
                return rad[i].strip().strip('"')
        return ""

    saknade = [f for f, namn in KOLUMNER.items() if not any(n in index for n in namn)]
    if "brunnsid" in saknade or "n" in saknade:
        raise RuntimeError(
            f"Oväntad kolumnuppsättning från SGU. Hittade: {', '.join(list(index)[:8])}"
        )

    rader: list[dict] = []
    lasta = 0
    utan_koordinat = 0
    sedda: set[str] = set()

    for rad in lasare:
        if not rad:
            continue
        lasta += 1
        brunnsid = plocka(rad, "brunnsid")
        n = _tal(plocka(rad, "n"))
        e = _tal(plocka(rad, "e"))
        if not brunnsid or n is None or e is None:
            utan_koordinat += 1
            continue
        if brunnsid in sedda:
            continue
        sedda.add(brunnsid)
        lat, lon = sweref99tm_to_wgs84(n, e)
        rader.append(
            {
                "brunnsid": brunnsid[:30],
                "lanskod": lanskod,
                "kommunkod": plocka(rad, "kommunkod")[:6],
                "n": n,
                "e": e,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "lagesnoggrannhet": plocka(rad, "lagesnoggrannhet")[:4],
                "fastighet": plocka(rad, "fastighet")[:120],
                "ort": plocka(rad, "ort")[:80],
                "borrdatum": normalisera_datum(plocka(rad, "borrdatum")),
                "totaldjup": _tal(plocka(rad, "totaldjup")),
                "djup_till_berg": _tal(plocka(rad, "djup_till_berg")),
                "vattenmangd": _tal(plocka(rad, "vattenmangd")),
                "grundvattenniva": _tal(plocka(rad, "grundvattenniva")),
                "foderror_till": _tal(plocka(rad, "foderror_till")),
                "anvandning": plocka(rad, "anvandning")[:10].upper(),
                "tatning": plocka(rad, "tatning")[:10],
                "anmarkning": plocka(rad, "anmarkning")[:255],
                "hamtad_at": started,
            }
        )
        if progress and len(rader) % 5000 == 0:
            progress(len(rader))

    await db.execute(delete(SguWell).where(SguWell.lanskod == lanskod))
    for i in range(0, len(rader), 2000):
        await db.execute(insert(SguWell), rader[i : i + 2000])
    await db.commit()

    return {
        "lanskod": lanskod,
        "namn": LAN_NAMN.get(lanskod, lanskod),
        "lasta": lasta,
        "sparade": len(rader),
        "utan_koordinat": utan_koordinat,
        "sekunder": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "kalla": url,
    }


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
