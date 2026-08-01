"""Offertmallar.

Tre mallar läggs in vid första start. De täcker det som en borrfirma gör oftast,
och är tänkta att ändras: rubrik, texter och rader går att skriva om, och egna
mallar går att skapa från en offert man redan gjort.

Raderna i en mall matchar mot artikelregistret på artikelnummer när offerten
skapas. Finns artikeln används dagens pris därifrån. Saknas den blir raden ändå
kvar med mallens pris, så att en mall aldrig tyst tappar innehåll.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import QuoteTemplate

STANDARDMALLAR = [
    {
        "name": "Bergborrad brunn med pump",
        "description": "Ny vattenbrunn inklusive installation. Den vanligaste offerten.",
        "title": "Bergborrad brunn med pumpinstallation",
        "intro": (
            "Tack för visat intresse. Nedan följer offert på bergborrad brunn med installation "
            "av pump, enligt vad vi kom överens om vid platsbesöket.\n\n"
            "Borrdjupet är uppskattat utifrån grannbrunnar i området. Verkligt djup kan avvika, "
            "och faktureras då enligt löpande meterpris."
        ),
        "terms": (
            "Priset förutsätter framkomlighet för borrigg fram till borrplatsen samt tillgång "
            "till el och vatten.\n"
            "Borrdjup faktureras enligt verklig längd. Angivet djup är en uppskattning.\n"
            "Eventuell sprängning, tryckning eller extra foderrör tillkommer efter överenskommelse.\n"
            "Betalningsvillkor 30 dagar netto. Dröjsmålsränta enligt räntelagen.\n"
            "Arbetet omfattas av ROT-avdrag för privatpersoner där förutsättningarna är uppfyllda."
        ),
        "valid_days": 30,
        "lines": [
            {"kind": "arbete", "name": "Etablering av borrigg", "unit": "st", "quantity": 1, "unit_price": 6500},
            {"kind": "arbete", "name": "Borrning i berg", "unit": "m", "quantity": 70, "unit_price": 420,
             "note": "Uppskattat djup, faktureras enligt verklig längd"},
            {"kind": "material", "name": "Foderrör, nedslaget till berg", "unit": "m", "quantity": 6, "unit_price": 465},
            {"kind": "material", "name": "Pumppaket med tryckkärl", "unit": "st", "quantity": 1, "unit_price": 18900},
            {"kind": "material", "name": "Pumpkabel och rep", "unit": "m", "quantity": 50, "unit_price": 42},
            {"kind": "arbete", "name": "Installation och driftsättning", "unit": "tim", "quantity": 6, "unit_price": 890},
            {"kind": "ovrigt", "name": "Vattenanalys, laboratorium", "unit": "st", "quantity": 1, "unit_price": 1450},
        ],
    },
    {
        "name": "Energibrunn för bergvärme",
        "description": "Borrning och kollektor för värmepump.",
        "title": "Energibrunn för bergvärme",
        "intro": (
            "Offert på energibrunn för bergvärme enligt platsbesök. Djupet är beräknat utifrån "
            "husets energibehov och bergförhållandena i området."
        ),
        "terms": (
            "Anmälan till kommunen ombesörjs av fastighetsägaren om inget annat avtalats.\n"
            "Priset förutsätter framkomlighet för borrigg samt att borrplatsen är utsatt.\n"
            "Kollektorslang trycksätts och täthetsprovas före överlämning.\n"
            "Betalningsvillkor 30 dagar netto."
        ),
        "valid_days": 30,
        "lines": [
            {"kind": "arbete", "name": "Etablering av borrigg", "unit": "st", "quantity": 1, "unit_price": 6500},
            {"kind": "arbete", "name": "Borrning energibrunn", "unit": "m", "quantity": 160, "unit_price": 380},
            {"kind": "material", "name": "Foderrör, nedslaget till berg", "unit": "m", "quantity": 6, "unit_price": 465},
            {"kind": "material", "name": "Kollektorslang med returböj", "unit": "m", "quantity": 165, "unit_price": 68},
            {"kind": "material", "name": "Köldbärarvätska", "unit": "l", "quantity": 120, "unit_price": 38},
            {"kind": "arbete", "name": "Trycksättning och täthetsprovning", "unit": "st", "quantity": 1, "unit_price": 2400},
        ],
    },
    {
        "name": "Pumpbyte",
        "description": "Byte av pump i befintlig brunn.",
        "title": "Byte av pump",
        "intro": (
            "Offert på byte av pump i befintlig brunn. Priset avser byte till likvärdig eller "
            "bättre pump, inklusive upptagning av den gamla."
        ),
        "terms": (
            "Om brunnen visar sig behöva rensning eller om röret är skadat tillkommer det efter "
            "överenskommelse.\n"
            "Den gamla pumpen tas om hand för återvinning om inget annat önskas.\n"
            "Betalningsvillkor 30 dagar netto."
        ),
        "valid_days": 30,
        "lines": [
            {"kind": "material", "name": "Pump med tillbehör", "unit": "st", "quantity": 1, "unit_price": 18900},
            {"kind": "material", "name": "Pumpkabel och rep", "unit": "m", "quantity": 50, "unit_price": 42},
            {"kind": "arbete", "name": "Upptagning av gammal pump", "unit": "tim", "quantity": 2, "unit_price": 890},
            {"kind": "arbete", "name": "Installation och driftsättning", "unit": "tim", "quantity": 3, "unit_price": 890},
            {"kind": "arbete", "name": "Framkörning", "unit": "st", "quantity": 1, "unit_price": 1200},
        ],
    },
]


def rader_ur(mall: QuoteTemplate) -> list[dict]:
    try:
        return json.loads(mall.lines or "[]")
    except json.JSONDecodeError:
        return []


async def se_till_att_mallar_finns(db: AsyncSession) -> int:
    """Lägger in standardmallarna första gången. Rör aldrig befintliga mallar."""
    antal = (await db.execute(select(func.count()).select_from(QuoteTemplate))).scalar() or 0
    if antal:
        return 0
    for i, m in enumerate(STANDARDMALLAR):
        db.add(
            QuoteTemplate(
                name=m["name"],
                description=m["description"],
                title=m["title"],
                intro=m["intro"],
                terms=m["terms"],
                valid_days=m["valid_days"],
                lines=json.dumps(m["lines"], ensure_ascii=False),
                is_builtin=True,
                sort_order=i,
                created_by="system",
            )
        )
    await db.commit()
    return len(STANDARDMALLAR)
