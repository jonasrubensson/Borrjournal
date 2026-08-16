"""Slår upp koordinater från en adress.

Kräver en extern tjänst. Det finns ingen rimlig väg att slå upp svenska adresser
offline utan att packa in ett adressregister, så detta är det enda i appen som
behöver internet. Fungerar det inte går koordinater alltid att skriva för hand.

Standard är OpenStreetMaps Nominatim. Deras villkor kräver en identifierbar
User-Agent och högst en förfrågan per sekund, vilket respekteras här. Kör du eget
Nominatim eller annan tjänst, peka om GEOCODER_URL.
"""

from __future__ import annotations

import asyncio
import time

from ..config import settings

_last_call = 0.0
_lock = asyncio.Lock()


async def _fraga(text: str) -> list:
    """Ett anrop mot tjänsten, med snällhetsgränsen respekterad."""
    import httpx

    global _last_call
    async with _lock:
        wait = 1.05 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                r = await client.get(
                    settings.geocoder_url,
                    params={
                        "q": text,
                        "format": "jsonv2",
                        "limit": 1,
                        "countrycodes": settings.geocoder_country_code,
                        "addressdetails": 1,
                    },
                    headers={
                        "User-Agent": settings.geocoder_user_agent,
                        "Accept-Language": "sv",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Nådde inte adresstjänsten: {exc}. Servern kanske saknar internetåtkomst."
            ) from exc

    if r.status_code in (403, 429):
        raise RuntimeError(
            "Adresstjänsten avvisade förfrågan. Sätt GEOCODER_USER_AGENT i .env till något "
            "som identifierar er, till exempel \"Borrjournal (namn@dinfirma.se)\"."
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Adresstjänsten svarade {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("Oväntat svar från adresstjänsten") from None


# Ord som visar att texten är en gatuadress och inte en fastighetsbeteckning
GATUORD = (
    "gatan", "gata", "vägen", "väg", "gränd", "torg", "backen", "stigen",
    "allén", "plan", "byn", "gårdsvägen",
)


def ser_ut_som_adress(text: str) -> bool:
    t = (text or "").lower()
    return any(ord_ in t for ord_ in GATUORD)


async def geocode(
    query: str, municipality: str = "", fastighet: str = ""
) -> dict | None:
    """Slår upp en adress. Provar flera formuleringar innan den ger upp.

    OpenStreetMap känner inte till svenska fastighetsbeteckningar. "Hasselmusen 2"
    finns inte i deras register, men däremot kan det finnas en plats som heter
    något liknande i en helt annan del av landet. Att söka på en beteckning utan
    kommun ger därför inte en osäker träff, den ger en felaktig träff som ser
    riktig ut. Sådana sökningar görs inte.

    Träffar kontrolleras också mot kommunen. Ligger svaret i fel kommun är det
    fel plats, hur bra det än matchar på namnet.
    """
    if not settings.geocoder_url:
        return None

    kommun = (municipality or "").strip()
    land = settings.geocoder_country_name
    grund = (query or "").strip()
    fast = (fastighet or "").strip()

    # Vad är det egentligen vi har att gå på?
    har_adress = bool(grund) and (ser_ut_som_adress(grund) or not fast)
    if not grund and fast:
        grund = fast
        har_adress = False

    if not grund:
        return None

    if not har_adress and not kommun:
        raise RuntimeError(
            "En fastighetsbeteckning går inte att slå upp utan kommun. "
            "Samma beteckning finns i flera kommuner, och ett uppslag utan kommun "
            "skulle peka på fel plats. Fyll i kommun, eller hämta din position på plats."
        )

    forsok = []
    if har_adress:
        if kommun:
            forsok.append(", ".join([grund, kommun, land]))
        forsok.append(", ".join([grund, land]))
        utan_nummer = " ".join(w for w in grund.split() if not any(c.isdigit() for c in w))
        if utan_nummer and utan_nummer != grund:
            forsok.append(", ".join(filter(None, [utan_nummer, kommun, land])))
    else:
        # Beteckning: bara inom kommunen, aldrig i hela landet
        forsok.append(", ".join([grund, kommun, land]))
        utan_nummer = " ".join(w for w in grund.split() if not any(c.isdigit() for c in w))
        if utan_nummer and utan_nummer != grund:
            forsok.append(", ".join([utan_nummer, kommun, land]))
    if kommun:
        forsok.append(", ".join([kommun, land]))

    def i_ratt_kommun(traff) -> bool:
        """En träff i fel kommun är fel plats, hur bra namnet än matchar."""
        if not kommun or not isinstance(traff, dict):
            return True
        return kommun.lower() in (traff.get("display_name") or "").lower()

    sett = set()
    hits = []
    anvand_text = ""
    sista_fel = None
    forkastade = []
    for text in forsok:
        if text in sett:
            continue
        sett.add(text)
        try:
            svar = await _fraga(text)
        except RuntimeError as exc:
            sista_fel = exc
            continue
        if isinstance(svar, dict):
            svar = svar.get("features") or []
        if not isinstance(svar, list) or not svar:
            continue

        # Behåll bara träffar i rätt kommun, annars vidare till nästa formulering
        godkanda = [t for t in svar if i_ratt_kommun(t)]
        if godkanda:
            hits = godkanda
            anvand_text = text
            break
        forkastade.append((text, (svar[0] or {}).get("display_name", "")))

    if not hits and sista_fel:
        raise sista_fel

    if not hits and forkastade:
        # Alla träffar låg i fel kommun. Bättre inget än fel plats.
        text, hittad = forkastade[0]
        raise RuntimeError(
            f"Hittade bara träffar utanför {kommun}, till exempel "
            f"{hittad.split(',')[0] if hittad else 'okänd plats'}. "
            "Kontrollera stavningen, eller hämta din position på plats."
        )

    # Tjänsten kan svara med ett felobjekt i stället för en lista
    if isinstance(hits, dict):
        hits = hits.get("features") or []
    if not isinstance(hits, list) or not hits:
        return None
    hit = hits[0]
    if not isinstance(hit, dict) or "lat" not in hit or "lon" not in hit:
        return None
    try:
        lat, lon = float(hit["lat"]), float(hit["lon"])
    except (TypeError, ValueError):
        return None

    etikett = hit.get("display_name", "")
    typ = (hit.get("addresstype") or hit.get("type") or "").lower()
    grov = typ in ("municipality", "town", "village", "city", "county", "administrative")

    # Hamnade träffen i rätt kommun? Annars är den fel plats.
    fel_kommun = bool(kommun) and kommun.lower() not in etikett.lower()  # ska vara False här

    if grov:
        precision = "kommun"
    elif typ in ("road", "residential", "street"):
        precision = "gata"
    elif typ in ("house", "building", "yes", "place"):
        precision = "adress"
    else:
        precision = typ or "okänd"

    return {
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "label": etikett,
        "short_label": ", ".join(etikett.split(",")[:3]).strip(),
        "precision": precision,
        "approximate": grov or fel_kommun or not har_adress,
        "wrong_municipality": fel_kommun,
        "query_used": anvand_text,
        "source": "nominatim",
        "warning": (
            f"Träffen ligger i {etikett.split(',')[-3].strip() if etikett.count(',') > 2 else etikett}, "
            f"inte i {kommun}. Kontrollera koordinaten."
            if fel_kommun
            else "Pekar på orten, inte på tomten."
            if grov
            else "Fastighetsbeteckningar finns inte i adressregistret, träffen är ungefärlig."
            if not har_adress
            else ""
        ),
    }


async def geocode_safe(
    query: str, municipality: str = "", fastighet: str = ""
) -> dict | None:
    """Som geocode men sväljer fel. För automatiska uppslag i bakgrunden,
    där ett misslyckande aldrig får hindra att posten sparas."""
    try:
        return await geocode(query, municipality, fastighet)
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] automatiskt adressuppslag misslyckades: {exc}")
        return None


# Länsnamn som Nominatim skriver dem, till länskod
LAN_TILL_KOD = {
    "stockholms län": "01", "uppsala län": "03", "södermanlands län": "04",
    "östergötlands län": "05", "jönköpings län": "06", "kronobergs län": "07",
    "kalmar län": "08", "gotlands län": "09", "blekinge län": "10",
    "skåne län": "12", "hallands län": "13", "västra götalands län": "14",
    "värmlands län": "17", "örebro län": "18", "västmanlands län": "19",
    "dalarnas län": "20", "gävleborgs län": "21", "västernorrlands län": "22",
    "jämtlands län": "23", "västerbottens län": "24", "norrbottens län": "25",
}


async def lan_for_punkt(lat: float, lon: float) -> dict | None:
    """Vilket län ligger punkten i?

    Används för att kunna hämta rätt SGU-data automatiskt i stället för att
    kräva att någon vet vilka län som behövs.
    """
    import httpx

    global _last_call
    if not settings.geocoder_url:
        return None

    url = settings.geocoder_url.replace("/search", "/reverse")
    async with _lock:
        wait = 1.05 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                r = await client.get(
                    url,
                    params={
                        "lat": lat,
                        "lon": lon,
                        "format": "jsonv2",
                        "zoom": 8,
                        "addressdetails": 1,
                    },
                    headers={
                        "User-Agent": settings.geocoder_user_agent,
                        "Accept-Language": "sv",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Nådde inte adresstjänsten: {exc}") from exc

    if r.status_code >= 400:
        raise RuntimeError(f"Adresstjänsten svarade {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        return None

    adress = (data or {}).get("address") or {}
    lan_namn = (adress.get("county") or adress.get("state") or "").strip().lower()
    kod = LAN_TILL_KOD.get(lan_namn)
    if not kod:
        # Nominatim kan skriva "Jönköping County" eller utan "län"
        for namn, k in LAN_TILL_KOD.items():
            kort = namn.replace(" län", "")
            if kort and kort in lan_namn:
                kod = k
                break
    if not kod:
        return None
    return {
        "lanskod": kod,
        "lan": lan_namn,
        "kommun": adress.get("municipality") or adress.get("city") or "",
    }
