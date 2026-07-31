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


async def geocode(query: str, municipality: str = "") -> dict | None:
    if not settings.geocoder_url or not query.strip():
        return None

    import httpx

    parts = [query.strip()]
    if municipality and municipality.lower() not in query.lower():
        parts.append(municipality.strip())
    parts.append(settings.geocoder_country_name)
    text = ", ".join(p for p in parts if p)

    global _last_call
    async with _lock:
        # Snällhetsgräns mot tjänsten
        wait = 1.05 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    settings.geocoder_url,
                    params={
                        "q": text,
                        "format": "jsonv2",
                        "limit": 1,
                        "countrycodes": settings.geocoder_country_code,
                        "addressdetails": 1,
                    },
                    headers={"User-Agent": settings.geocoder_user_agent},
                )
                r.raise_for_status()
                hits = r.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Adressuppslag misslyckades: {exc}") from exc

    if not hits:
        return None
    hit = hits[0]
    return {
        "latitude": round(float(hit["lat"]), 6),
        "longitude": round(float(hit["lon"]), 6),
        "label": hit.get("display_name", ""),
        "source": "nominatim",
    }
