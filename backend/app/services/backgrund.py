"""Adressuppslag som sker efter att svaret gått iväg.

Uppslaget mot en extern karttjänst kan ta tiotals sekunder om tjänsten är trög,
och fyra nedtrappade försök i rad blir i värsta fall en minut. Görs det inne i
sparandet hänger begäran, och en proxy framför hinner ge upp med 502 eller 504
innan användaren fått veta om besöket ens sparades.

Därför: spara först, slå upp sedan. Posten får en status som gränssnittet visar,
och ett misslyckande blir ett meddelande på posten i stället för ett fel som ser
ut som att ingenting fungerade.
"""

from __future__ import annotations

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Facility, Visit
from . import events
from .geocode import geocode


async def slag_upp_adress(typ: str, objekt_id: str) -> None:
    """Kör i bakgrunden. Skriver resultatet på posten."""
    modell = Visit if typ == "visit" else Facility
    async with SessionLocal() as db:
        obj = (
            await db.execute(select(modell).where(modell.id == objekt_id))
        ).unique().scalar_one_or_none()
        if obj is None:
            return
        if obj.latitude is not None and obj.longitude is not None:
            obj.geocode_status = "klar"
            await db.commit()
            return

        # Gatuadress och fastighetsbeteckning skickas var för sig. Beteckningar
        # finns inte i adressregistret och kräver kommun för att inte peka fel.
        if typ == "visit":
            gata = obj.address or ""
            fastighet = obj.property_designation or ""
            kommun = obj.municipality or ""
        else:
            kund = obj.customer
            gata = (kund.address if kund else "") or ""
            fastighet = (kund.property_designation if kund else "") or ""
            kommun = (kund.municipality if kund else "") or ""

        adress = gata or fastighet
        if not adress:
            obj.geocode_status = ""
            obj.geocode_message = ""
            await db.commit()
            return

        try:
            hit = await geocode(gata, kommun, fastighet=fastighet)
        except Exception as exc:  # noqa: BLE001
            obj.geocode_status = "misslyckades"
            obj.geocode_message = str(exc)[:255]
            await events.logga(
                db,
                level="varning",
                source="adressuppslag",
                message=f"Kunde inte slå upp {adress}",
                detail=(
                    f"{exc}\n\nKoordinaten går att fylla i för hand, eller hämtas med "
                    "Hämta min position när du står på plats."
                ),
                object_type=typ,
                object_id=objekt_id,
                commit=False,
            )
            await db.commit()
            return

        if not hit:
            obj.geocode_status = "misslyckades"
            obj.geocode_message = (
                f"Hittade ingen träff på {adress}. Skriv koordinaten för hand, "
                "eller hämta din position när du står på plats."
            )[:255]
            await db.commit()
            return

        # Brunnsarkivet känner fastighetsbeteckningar, det gör inte adressregistret.
        # Finns det redan en brunn på fastigheten ligger den på tomten, vilket är
        # oändligt mycket bättre än ortens mittpunkt.
        if fastighet and hit.get("approximate"):
            from .sgu import sok_fastighet

            traff = await sok_fastighet(
                db, fastighet, hit["latitude"], hit["longitude"], radie_km=40
            )
            if traff:
                obj.latitude = traff["latitude"]
                obj.longitude = traff["longitude"]
                if not obj.coordinates:
                    obj.coordinates = f"{traff['latitude']}, {traff['longitude']}"
                obj.geocode_status = "klar" if traff["exakt_beteckning"] else "ungefarlig"
                obj.geocode_message = (
                    f"Hittad i Brunnsarkivet: {traff['fastighet']}"
                    + (f", {traff['ort']}" if traff["ort"] else "")
                    + f". {traff['antal_brunnar']} registrerad"
                    + ("e brunnar" if traff["antal_brunnar"] > 1 else " brunn")
                    + " på fastigheten."
                    + ("" if traff["exakt_beteckning"] else " Matchar traktnamnet, inte hela beteckningen.")
                )[:255]
                await db.commit()
                return

        obj.latitude = hit["latitude"]
        obj.longitude = hit["longitude"]
        if not obj.coordinates:
            obj.coordinates = f"{hit['latitude']}, {hit['longitude']}"
        obj.geocode_status = "ungefarlig" if hit.get("approximate") else "klar"
        obj.geocode_message = (
            f"Hittade {hit.get('short_label', '')}."
            + (f" {hit['warning']}" if hit.get("warning") else "")
        )[:255]

        # En träff i fel kommun eller på ortnivå är värd en anteckning, annars
        # tror man att koordinaten pekar på tomten.
        if hit.get("wrong_municipality") or hit.get("precision") == "kommun":
            await events.logga(
                db,
                level="varning",
                source="adressuppslag",
                message=f"Ungefärlig koordinat för {adress}",
                detail=(
                    f"{hit.get('warning', '')}\n\nSökningen som gav träff: "
                    f"{hit.get('query_used', '')}\nTräff: {hit.get('label', '')}\n\n"
                    "Justera med Hämta min position när du står på plats."
                ),
                object_type=typ,
                object_id=objekt_id,
                commit=False,
            )
        await db.commit()
