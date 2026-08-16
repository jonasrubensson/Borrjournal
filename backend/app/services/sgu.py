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
# Många rader i Brunnsarkivet saknar användningskod. De är i praktiken
# vattenbrunnar, och att lämna dem utanför gör att en femtedel av materialet
# försvinner ur borrdjup och kapacitet trots att de räknas i antalet.
EJ_VATTEN = {"ENE", "OBS"}


def ar_vattenbrunn(kod: str) -> bool:
    return (kod or "").strip().upper() not in EJ_VATTEN

# SGU:s bedömning av hur nära det angivna läget ligger verkligheten
LAGESNOGGRANNHET = {
    "0": "avviker mindre än 100 m",
    "1": "avviker mindre än 250 m",
    "2": "osäkert läge",
    "3": "läget inte kontrollerat",
}

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
    "tecken_jord": ("TJ",),
    "tecken_vatten": ("TV",),
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
                "tecken_jord": plocka(rad, "tecken_jord")[:2],
                "tecken_vatten": plocka(rad, "tecken_vatten")[:2],
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
    db: AsyncSession,
    lat: float,
    lon: float,
    radius_m: float = 1000,
    limit: int | None = None,
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
                "lagesnoggrannhet_text": LAGESNOGGRANNHET.get(
                    (w.lagesnoggrannhet or "").strip(), "okänd noggrannhet"
                ),
                "berg_minst": w.tecken_jord == ">",
                "kapacitet_minst": w.tecken_vatten == ">",
                "kapacitet_hogst": w.tecken_vatten == "<",
            }
        )
    traffar.sort(key=lambda x: x["avstand_m"])
    # Utan gräns som standard. En tyst avkapning gör att brunnar i ett tätt
    # område försvinner ur statistiken utan att någon märker det.
    return traffar[:limit] if limit else traffar


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


def normalisera_beteckning(text: str) -> str:
    """Gör beteckningar jämförbara: versaler, ett mellanslag, inget skräp.

    SGU skriver dem som "VÄSSLAN 3:14". Folk skriver "Vässlan 3:14",
    "vasslan 3: 14" eller bara "Vässlan 3".
    """
    rent = " ".join((text or "").upper().split())
    rent = rent.replace(" :", ":").replace(": ", ":")
    return rent.strip()


def _namndel(beteckning: str) -> str:
    """Traktnamnet utan blocknummer, alltså VÄSSLAN ur VÄSSLAN 3:14."""
    delar = normalisera_beteckning(beteckning).split()
    return delar[0] if delar else ""


async def sok_fastighet(
    db: AsyncSession,
    beteckning: str,
    nara_lat: float | None = None,
    nara_lon: float | None = None,
    radie_km: float = 40,
) -> dict | None:
    """Letar upp en fastighet i Brunnsarkivet och ger dess läge.

    Brunnsarkivet innehåller fastighetsbeteckningen för varje registrerad brunn.
    Finns det redan en brunn på fastigheten är dess koordinat vida mycket bättre
    än ortens mittpunkt, för den ligger på tomten.

    Samma beteckning förekommer i flera kommuner, därför krävs en ungefärlig
    utgångspunkt att söka runt. Den kommer från kommunuppslaget.
    """
    sokt = normalisera_beteckning(beteckning)
    if len(sokt) < 3:
        return None

    stmt = select(SguWell).where(SguWell.fastighet != "")
    if nara_lat is not None and nara_lon is not None:
        n0, e0 = wgs84_to_sweref99tm(nara_lat, nara_lon)
        meter = radie_km * 1000
        stmt = stmt.where(
            SguWell.n.between(n0 - meter, n0 + meter),
            SguWell.e.between(e0 - meter, e0 + meter),
        )
    rader = (await db.execute(stmt)).scalars().all()
    if not rader:
        return None

    exakta = []
    ungefarliga = []
    namn_sokt = _namndel(sokt)
    for w in rader:
        varde = normalisera_beteckning(w.fastighet)
        if varde == sokt:
            exakta.append(w)
        elif namn_sokt and len(namn_sokt) >= 4 and _namndel(varde) == namn_sokt:
            ungefarliga.append(w)

    traffar = exakta or ungefarliga
    if not traffar:
        return None

    # Ligger träffarna långt isär är beteckningen tvetydig och duger inte
    n_medel = sum(w.n for w in traffar) / len(traffar)
    e_medel = sum(w.e for w in traffar) / len(traffar)
    spridning = max(math.hypot(w.n - n_medel, w.e - e_medel) for w in traffar)
    if spridning > 3000:
        return None

    lat, lon = sweref99tm_to_wgs84(n_medel, e_medel)
    return {
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "antal_brunnar": len(traffar),
        "exakt_beteckning": bool(exakta),
        "spridning_m": round(spridning),
        "fastighet": traffar[0].fastighet,
        "ort": traffar[0].ort,
        "source": "sgu",
    }


async def briefing(
    db: AsyncSession, lat: float, lon: float, radius_m: float = 1000
) -> dict:
    """Underlaget inför ett besök: vad grannarna stötte på och vad de fick."""
    alla = await neighbours(db, lat, lon, radius_m)
    vatten = [w for w in alla if ar_vattenbrunn(w["anvandning"])]
    energi = [w for w in alla if w["anvandning"] == "ENE"]
    okand_typ = len([w for w in alla if not (w["anvandning"] or "").strip()])

    # Ett jorddjup med ">" betyder att berget ligger djupare, hur mycket vet
    # ingen. Att räkna in det som ett uppmätt värde drar ner medianen och ger
    # en för billig offert. De redovisas separat i stället.
    uppmatt = [w for w in alla if not w["berg_minst"]]
    minst = [w for w in alla if w["berg_minst"]]
    berg = _sammanfatta([w["djup_till_berg"] for w in uppmatt])
    berg_minst = _sammanfatta([w["djup_till_berg"] for w in minst])
    # Foderrörslängd är det som avgör priset mest, och det är grannarnas verkliga
    # längder som betyder något, inte en uppskattning ur jorddjupet.
    foderror = _sammanfatta([w["foderror_till"] for w in alla])
    djup_vatten = _sammanfatta([w["totaldjup"] for w in vatten])
    djup_energi = _sammanfatta([w["totaldjup"] for w in energi])
    kapacitet = _sammanfatta([w["vattenmangd"] for w in vatten])
    antal_osakert_lage = len(
        [w for w in alla if (w["lagesnoggrannhet"] or "").strip() in ("2", "3")]
    )
    niva = _sammanfatta([w["grundvattenniva"] for w in alla])

    kapaciteter = [w["vattenmangd"] for w in vatten if w["vattenmangd"]]
    svaga = [v for v in kapaciteter if v < 600]

    # Vilken granne krävde mest foderrör, och var ligger den?
    varsta_foderror = None
    med_foderror = [w for w in alla if w["foderror_till"]]
    if med_foderror:
        varsta = max(med_foderror, key=lambda w: w["foderror_till"])
        varsta_foderror = {
            "meter": varsta["foderror_till"],
            "avstand_m": varsta["avstand_m"],
            "fastighet": varsta["fastighet"],
            "borrdatum": varsta["borrdatum"],
            "djup_till_berg": varsta["djup_till_berg"],
        }

    # Skilj på "inget borrat här" och "vi har inte hämtat data för trakten".
    # Utan den skillnaden ser en tom cache ut som en oborrad bygd.
    from sqlalchemy import func as _func

    totalt = (
        await db.execute(select(_func.count()).select_from(SguWell))
    ).scalar() or 0

    narmast_km = None
    if totalt and not alla:
        # Hur långt bort ligger närmaste hämtade brunn? Är det tiotals mil
        # har man hämtat fel län, inte hamnat i en oborrad trakt.
        n0, e0 = wgs84_to_sweref99tm(lat, lon)
        rad = (
            await db.execute(
                select(SguWell.n, SguWell.e)
                .where(
                    SguWell.n.between(n0 - 200000, n0 + 200000),
                    SguWell.e.between(e0 - 200000, e0 + 200000),
                )
                .limit(4000)
            )
        ).all()
        if rad:
            narmast_km = round(
                min(math.hypot(r[0] - n0, r[1] - e0) for r in rad) / 1000, 1
            )

    # Ligger punkten i ett län vi inte hämtat? Då är det den enda relevanta
    # förklaringen, och den ska visas i klartext i stället för att användaren
    # ska behöva räkna ut det själv.
    saknat_lan = None
    if not alla:
        try:
            from .geocode import lan_for_punkt

            traff = await lan_for_punkt(lat, lon)
        except Exception:  # noqa: BLE001
            traff = None
        if traff:
            hamtade_koder = {
                r[0] for r in (await db.execute(select(SguWell.lanskod).distinct())).all()
            }
            if traff["lanskod"] not in hamtade_koder:
                saknat_lan = {
                    "lanskod": traff["lanskod"],
                    "namn": LAN_NAMN.get(traff["lanskod"], traff["lan"]),
                    "kommun": traff.get("kommun", ""),
                }

    hamtade_lan = sorted(
        {
            r[0]
            for r in (await db.execute(select(SguWell.lanskod).distinct())).all()
            if r[0]
        }
    )

    return {
        "radius_m": radius_m,
        "antal": len(alla),
        "origin_lat": lat,
        "origin_lon": lon,
        "cache_totalt": totalt,
        "hamtade_lan": [
            {"kod": k, "namn": LAN_NAMN.get(k, k)} for k in hamtade_lan
        ],
        "narmaste_hamtade_km": narmast_km,
        "saknar_data": totalt == 0,
        "saknat_lan": saknat_lan,
        # Långt till närmaste hämtade brunn betyder nästan alltid fel län
        "troligen_fel_lan": bool(narmast_km and narmast_km > 40),
        "antal_vattenbrunnar": len(vatten),
        "antal_utan_typ": okand_typ,
        "antal_energibrunnar": len(energi),
        "jorddjup": berg,
        "jorddjup_minst": berg_minst,
        "antal_jordbrunnar": len(minst),
        "antal_osakert_lage": antal_osakert_lage,
        "foderror": foderror,
        "borrdjup_vatten": djup_vatten,
        "borrdjup_energi": djup_energi,
        "kapacitet": kapacitet,
        "grundvattenniva": niva,
        "svag_kapacitet_andel": round(100 * len(svaga) / len(kapaciteter)) if kapaciteter else None,
        "varsta_foderror": varsta_foderror,
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
