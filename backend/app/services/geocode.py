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


async def geocode(query: str, municipality: str = "") -> dict | None:
    """Slår upp en adress. Provar flera formuleringar innan den ger upp.

    En fastighetsbeteckning som "Ekbacken 2:5" finns sällan i adressregister, medan
    ortnamnet gör det. Därför trappas frågan ner tills något träffar.
    """
    if not settings.geocoder_url or not query.strip():
        return None

    kommun = (municipality or "").strip()
    land = settings.geocoder_country_name

    forsok = []
    grund = query.strip()
    if kommun and kommun.lower() not in grund.lower():
        forsok.append(", ".join([grund, kommun, land]))
    forsok.append(", ".join([grund, land]))
    # Utan husnummer, ofta det som fäller uppslaget på landsbygden
    utan_nummer = " ".join(w for w in grund.split() if not any(c.isdigit() for c in w))
    if utan_nummer and utan_nummer != grund:
        forsok.append(", ".join(filter(None, [utan_nummer, kommun, land])))
    # Sista utväg: bara kommunen, vilket åtminstone ger rätt trakt
    if kommun:
        forsok.append(", ".join([kommun, land]))

    sett = set()
    hits = []
    sista_fel = None
    for text in forsok:
        if text in sett:
            continue
        sett.add(text)
        try:
            hits = await _fraga(text)
        except RuntimeError as exc:
            sista_fel = exc
            continue
        if hits:
            break

    if not hits and sista_fel:
        raise sista_fel

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
    return {
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "label": etikett,
        "short_label": ", ".join(etikett.split(",")[:3]).strip(),
        # Grov träff betyder att vi fick kommunen eller orten, inte adressen
        "precision": hit.get("addresstype") or hit.get("type") or "",
        "approximate": (hit.get("addresstype") or "") in ("municipality", "town", "village", "city", "county"),
        "source": "nominatim",
    }


async def geocode_safe(query: str, municipality: str = "") -> dict | None:
    """Som geocode men sväljer fel. För automatiska uppslag i bakgrunden,
    där ett misslyckande aldrig får hindra att posten sparas."""
    try:
        return await geocode(query, municipality)
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] automatiskt adressuppslag misslyckades: {exc}")
        return None
