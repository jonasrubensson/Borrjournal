"""Revisionsloggen.

Bevisvärdet i en enkel elektronisk signatur står och faller med loggen. Tre
saker gör den svår att ifrågasätta:

* **Hashkedja.** Varje post innehåller hashen av föregående post. Ändras en rad
  i efterhand stämmer inte kedjan, och det går att visa. Det skyddar även mot
  att den som driver tjänsten skriver om historien.
* **Allt sparas, inte bara signaturen.** När länken skickades, när den öppnades,
  varifrån, vilken kod som begärdes, hur många försök. Ett förnekande måste
  förklara hela kedjan, inte bara ett klick.
* **Dokumentets hash.** Vi sparar en hash av exakt den PDF som visades. Det
  besvarar frågan "vad var det egentligen som godkändes".
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Handelse

TEXTER = {
    "skapad": "Signeringslänk skapad",
    "utskick": "Länk skickad med e-post",
    "oppnad": "Länken öppnad",
    "kod_begard": "Engångskod begärd",
    "kod_skickad": "Engångskod skickad med e-post",
    "kod_fel": "Fel engångskod angiven",
    "kod_ok": "Engångskod godkänd, identiteten knuten till e-postadressen",
    "dokument_visat": "Dokumentet visat i webbläsaren",
    "signerad": "Dokumentet godkänt och signerat",
    "avbojd": "Dokumentet avböjt",
    "kvitto_skickat": "Kvittens skickad till mottagaren",
    "utgangen": "Länken har gått ut",
}


def _hasha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def logga(
    db: AsyncSession,
    signering_id: str,
    typ: str,
    *,
    ip: str = "",
    webblasare: str = "",
    epost: str = "",
    beskrivning: str = "",
    commit: bool = True,
) -> Handelse:
    """Lägger till en post sist i kedjan."""
    forra = (
        await db.execute(
            select(Handelse)
            .where(Handelse.signering_id == signering_id)
            .order_by(Handelse.lopnummer.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    lopnummer = (forra.lopnummer + 1) if forra else 1
    foregaende = forra.hash if forra else ""
    tid = datetime.now(timezone.utc)

    innehall = json.dumps(
        {
            "signering": signering_id,
            "lopnummer": lopnummer,
            "typ": typ,
            "beskrivning": beskrivning or TEXTER.get(typ, typ),
            "ip": ip,
            "webblasare": webblasare[:255],
            "epost": epost,
            "at": tid.isoformat(),
            "foregaende": foregaende,
        },
        sort_keys=True,
        ensure_ascii=False,
    )

    post = Handelse(
        signering_id=signering_id,
        lopnummer=lopnummer,
        typ=typ,
        beskrivning=beskrivning or TEXTER.get(typ, typ),
        ip=ip[:64],
        webblasare=webblasare[:255],
        epost=epost[:200],
        at=tid,
        foregaende_hash=foregaende,
        hash=_hasha(innehall),
    )
    db.add(post)
    if commit:
        await db.commit()
    return post


async def kedja(db: AsyncSession, signering_id: str) -> list[Handelse]:
    return list(
        (
            await db.execute(
                select(Handelse)
                .where(Handelse.signering_id == signering_id)
                .order_by(Handelse.lopnummer)
            )
        )
        .scalars()
        .all()
    )


async def kontrollera_kedja(db: AsyncSession, signering_id: str) -> dict:
    """Räknar om hela kedjan och säger om någon post ändrats i efterhand."""
    poster = await kedja(db, signering_id)
    foregaende = ""
    for p in poster:
        innehall = json.dumps(
            {
                "signering": p.signering_id,
                "lopnummer": p.lopnummer,
                "typ": p.typ,
                "beskrivning": p.beskrivning,
                "ip": p.ip,
                "webblasare": p.webblasare,
                "epost": p.epost,
                "at": (
                    p.at if p.at.tzinfo else p.at.replace(tzinfo=timezone.utc)
                ).isoformat(),
                "foregaende": foregaende,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        if _hasha(innehall) != p.hash or p.foregaende_hash != foregaende:
            return {
                "hel": False,
                "antal": len(poster),
                "bruten_vid": p.lopnummer,
                "text": f"Loggen har ändrats vid post {p.lopnummer}.",
            }
        foregaende = p.hash
    return {
        "hel": True,
        "antal": len(poster),
        "sista_hash": foregaende,
        "text": f"Kedjan är obruten över {len(poster)} poster.",
    }


async def tidsstampla(data: bytes, url: str = "") -> dict | None:
    """Begär en tidsstämpel från en extern tjänst (RFC 3161).

    Bevisar att dokumentet fanns vid en viss tidpunkt och inte ändrats sedan
    dess, intygat av någon annan än oss. Misslyckas det fortsätter signeringen
    ändå, tidsstämpeln är ett tillägg och inte en förutsättning.
    """
    if not url:
        return None
    try:
        import httpx
        from asn1crypto import tsp
        from asn1crypto.algos import DigestAlgorithm

        digest = hashlib.sha256(data).digest()
        begaran = tsp.TimeStampReq(
            {
                "version": 1,
                "message_imprint": tsp.MessageImprint(
                    {
                        "hash_algorithm": DigestAlgorithm({"algorithm": "sha256"}),
                        "hashed_message": digest,
                    }
                ),
                "cert_req": True,
            }
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                content=begaran.dump(),
                headers={"Content-Type": "application/timestamp-query"},
            )
        r.raise_for_status()
        return {
            "svar": r.content.hex(),
            "tjanst": url,
            "hash": digest.hex(),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[signering] tidsstämpling misslyckades: {exc}", flush=True)
        return None
