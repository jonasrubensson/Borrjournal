"""Koppling mot signeringstjänsten.

All trafik går ut från Borrjournal. Tjänsten kan aldrig anropa oss, den kan
bara svara när vi frågar. Det gör att Borrjournal kan ligga kvar bakom mTLS
utan någon öppning inåt.

Flödet:

1. Vi skickar offertens PDF och mottagarens e-post
2. Tjänsten svarar med en länk som vi mejlar till kunden
3. Schemaläggaren frågar med jämna mellanrum om något hänt
4. När kunden signerat hämtar vi hem den signerade PDF:en och hela loggen,
   sparar dem hos kunden, och säger till att tjänsten får radera sin kopia
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import JournalEntry, Quote, StoredFile


NYCKEL_INSTALLNING = "signering"
STANDARD = {"url": "", "nyckel": "", "publik_url": "", "pa": False}

# Läses in vid start och när inställningarna sparas. Miljövariabler vinner,
# så att den som hellre håller hemligheter utanför databasen kan göra det.
_konf: dict = {}


async def las_installningar(db: AsyncSession) -> dict:
    """Hämtar inställningarna, med .env som åsidosättning."""
    from .notify import get_setting

    global _konf
    _konf = await get_setting(db, NYCKEL_INSTALLNING, STANDARD)
    return aktuell()


def aktuell() -> dict:
    """Vad som faktiskt används just nu."""
    url = settings.signering_url or _konf.get("url", "")
    nyckel = settings.signering_nyckel or _konf.get("nyckel", "")
    return {
        "url": url,
        "nyckel": nyckel,
        "publik_url": _konf.get("publik_url", ""),
        "fran_env": bool(settings.signering_url),
        "pa": bool(url and len(nyckel) >= 24 and _konf.get("pa", bool(settings.signering_url))),
    }


def aktiverad() -> bool:
    a = aktuell()
    return bool(a["url"] and len(a["nyckel"]) >= 24 and a["pa"])


def _huvuden() -> dict:
    return {"X-Delad-Nyckel": aktuell()["nyckel"]}


async def synka_installningar(db: AsyncSession) -> bool:
    """Skickar Borrjournals e-postuppgifter till signeringstjänsten.

    Utan detta har tjänsten inget sätt att skicka engångskoden, och kunden
    fastnar direkt. Anropas när en signering skapas och när inställningarna
    ändras, så att det aldrig glöms bort.
    """
    import httpx

    if not aktiverad():
        return False
    from .notify import DEFAULT_SMTP, SMTP_KEY, get_setting

    smtp = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    if not smtp.get("host"):
        return False
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.put(
                f"{aktuell()['url'].rstrip('/')}/api/installningar",
                headers=_huvuden(),
                json={
                    "smtp": {
                        "host": smtp.get("host", ""),
                        "port": smtp.get("port", 587),
                        "username": smtp.get("username", ""),
                        "password": smtp.get("password", ""),
                        "sender": smtp.get("sender", ""),
                        "security": smtp.get("security", "starttls"),
                    }
                },
            )
        return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] kunde inte skicka mejlinställningar: {exc}", flush=True)
        return False


async def sjalvtest(db: AsyncSession, till: str = "") -> dict:
    """Kontrollerar hela uppsättningen innan en kund berörs."""
    import httpx

    if not aktiverad():
        return {"aktiverad": False}
    await synka_installningar(db)
    async with httpx.AsyncClient(timeout=40.0) as client:
        r = await client.get(
            f"{aktuell()['url'].rstrip('/')}/api/sjalvtest",
            headers=_huvuden(),
            params={"till": till} if till else None,
        )
        r.raise_for_status()
    ut = r.json()
    ut["aktiverad"] = True
    return ut


async def skicka_for_signering(
    db: AsyncSession,
    *,
    quote: Quote,
    pdf: bytes,
    foretag: dict,
    belopp_text: str,
    giltig_dagar: int = 30,
    avsandare_person: str = "",
    avsandare_epost: str = "",
) -> dict:
    """Lämnar offerten till signeringstjänsten och får en länk tillbaka."""
    import httpx

    if not aktiverad():
        raise RuntimeError(
            "Signeringstjänsten är inte konfigurerad. Sätt SIGNERING_URL och "
            "SIGNERING_NYCKEL i .env."
        )

    # Se till att tjänsten kan skicka e-post innan vi skapar något
    await synka_installningar(db)

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{aktuell()['url'].rstrip('/')}/api/ny",
            headers=_huvuden(),
            json={
                "referens": quote.quote_no,
                "rubrik": quote.title or "Offert",
                "avsandare": foretag.get("namn", ""),
                "avsandare_person": avsandare_person,
                "avsandare_epost": avsandare_epost or foretag.get("epost", ""),
                "belopp": 0.0,
                "belopp_text": belopp_text,
                "mottagare_epost": quote.recipient_email,
                "mottagare_namn": quote.recipient_name,
                "pdf_base64": base64.b64encode(pdf).decode(),
                "giltig_dagar": giltig_dagar,
                "text_sida": foretag.get("signering_text_sida", ""),
                "text_godkann": foretag.get("signering_text_godkann", ""),
                "text_bevis": foretag.get("signering_text_bevis", ""),
            },
        )
    if r.status_code >= 400:
        try:
            detalj = r.json().get("detail", r.text[:200])
        except Exception:  # noqa: BLE001
            detalj = r.text[:200]
        raise RuntimeError(f"Signeringstjänsten svarade {r.status_code}: {detalj}")
    return r.json()


async def hamta_resultat(db: AsyncSession) -> dict:
    """Frågar om något signerats, och sparar i så fall hem allt.

    Anropas av schemaläggaren. Misslyckas anropet händer ingenting, nästa
    körning försöker igen.
    """
    import httpx

    if not aktiverad():
        return {"hamtade": 0}

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(
            f"{aktuell()['url'].rstrip('/')}/api/resultat", headers=_huvuden()
        )
        r.raise_for_status()
        svar = r.json()

        klara = []
        for post in svar.get("poster", []):
            try:
                await _ta_emot(db, post)
                klara.append(post["id"])
            except Exception as exc:  # noqa: BLE001
                print(f"[borrjournal] kunde inte spara signering: {exc}", flush=True)

        # Kvittera först när det är sparat hos oss. Går något fel ligger det
        # kvar hos tjänsten och kommer med nästa gång.
        if klara:
            await client.post(
                f"{aktuell()['url'].rstrip('/')}/api/kvittera",
                headers=_huvuden(),
                json={"ids": klara},
            )
    return {"hamtade": len(klara)}


def _spara_fil(db, quote, data: bytes, filnamn: str, beskrivning: str) -> None:
    """Lägger en PDF bland kundens dokument."""
    import uuid as _uuid

    from ..routers.files import make_pdf_thumb

    os.makedirs(os.path.join(settings.data_dir, "files"), exist_ok=True)
    lagrat = f"{_uuid.uuid4()}.pdf"
    with open(os.path.join(settings.data_dir, "files", lagrat), "wb") as fh:
        fh.write(data)
    db.add(
        StoredFile(
            customer_id=quote.customer_id,
            facility_id=quote.facility_id,
            filename=filnamn,
            stored_name=lagrat,
            thumb_name=make_pdf_thumb(data, lagrat),
            content_type="application/pdf",
            kind="dokument",
            size_bytes=len(data),
            caption=beskrivning,
            uploaded_by="Signeringstjänsten",
        )
    )


async def _ta_emot(db: AsyncSession, post: dict) -> None:
    """Sparar signerad PDF och logg hos kunden."""
    quote = (
        await db.execute(select(Quote).where(Quote.quote_no == post["referens"]))
    ).scalar_one_or_none()
    if quote is None:
        raise RuntimeError(f"Hittar ingen offert {post['referens']}")

    signerad = post.get("status") == "signerad"
    rader = post.get("handelser") or []
    logg = "\n".join(
        f"{h['nr']}. {h['at'][:19].replace('T', ' ')}  {h['beskrivning']}"
        + (f"  [{h['ip']}]" if h.get("ip") else "")
        for h in rader
    )

    # Spara loggen strukturerat, så att den går att visa på offerten i stället
    # för att bara ligga som text i en journalanteckning.
    import json as _json

    quote.signing_log = _json.dumps(rader, ensure_ascii=False)[:60000]
    quote.signing_pending = False
    quote.signing_hash = post.get("pdf_hash", "")[:64]
    quote.signing_hash_signerad = post.get("signerad_pdf_hash", "")[:64]
    quote.signing_chain_ok = bool(post.get("kedja_hel", True))
    quote.signed_by = (post.get("mottagare_epost") or "")[:200]

    if signerad:
        quote.status = "accepterad"
        quote.decided_at = (post.get("signerad_at") or "")[:10]
        quote.signed_at = (post.get("signerad_at") or "")[:30]
    elif post.get("status") == "avbojd":
        quote.status = "avslagen"
        quote.decided_at = datetime.now(timezone.utc).date().isoformat()

    fil_id = None
    if signerad and post.get("signerad_pdf_base64") and quote.customer_id:
        # Originalet sparas också. Utan det går kontrollsumman inte att
        # verifiera, och en summa som inte går att kontrollera är värdelös.
        if post.get("original_pdf_base64"):
            _spara_fil(
                db,
                quote,
                base64.b64decode(post["original_pdf_base64"]),
                f"{quote.quote_no}.pdf",
                "Det kunden godkände. Kontrollsumman på beviset gäller den här filen.",
            )
        pdf = base64.b64decode(post["signerad_pdf_base64"])
        os.makedirs(os.path.join(settings.data_dir, "files"), exist_ok=True)
        lagrat = f"{uuid.uuid4()}.pdf"
        with open(os.path.join(settings.data_dir, "files", lagrat), "wb") as fh:
            fh.write(pdf)

        from ..routers.files import make_pdf_thumb

        fil = StoredFile(
            customer_id=quote.customer_id,
            facility_id=quote.facility_id,
            filename=f"{quote.quote_no} signerad offert.pdf",
            stored_name=lagrat,
            thumb_name=make_pdf_thumb(pdf, lagrat),
            content_type="application/pdf",
            kind="dokument",
            size_bytes=len(pdf),
            caption=f"Signerad av {post.get('mottagare_epost', '')}",
            uploaded_by="Signeringstjänsten",
        )
        db.add(fil)
        await db.flush()
        fil_id = fil.id

    if quote.customer_id:
        db.add(
            JournalEntry(
                customer_id=quote.customer_id,
                facility_id=quote.facility_id,
                entry_type="Offert",
                title=(
                    f"Offert {quote.quote_no} signerad av {post.get('mottagare_epost', '')}"
                    if signerad
                    else f"Offert {quote.quote_no} avböjd"
                ),
                body=(
                    (
                        f"Signerad {(post.get('signerad_at') or '')[:19].replace('T', ' ')}.\n"
                        f"Dokumentets kontrollsumma: {post.get('pdf_hash', '')}\n"
                        f"Revisionskedjan: {'obruten' if post.get('kedja_hel') else 'BRUTEN'}\n\n"
                        if signerad
                        else (post.get("avbojd_orsak") or "Ingen orsak angiven") + "\n\n"
                    )
                    + "Händelseförlopp:\n"
                    + logg
                ),
                author_name="Signeringstjänsten",
            )
        )
    await db.commit()

    if not post.get("kedja_hel", True):
        from . import events

        await events.logga(
            db,
            level="fel",
            source="signering",
            message=f"Revisionskedjan för {post['referens']} är bruten",
            detail=(
                "Loggen hos signeringstjänsten stämmer inte med sina egna "
                "kontrollsummor. Det kan betyda att någon ändrat i den. "
                "Bevisvärdet är försvagat."
            ),
        )


async def aterkalla(db: AsyncSession, quote) -> dict:
    """Drar tillbaka en offert hos signeringstjänsten."""
    import httpx

    if not aktiverad():
        return {"aterkallade": 0}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{aktuell()['url'].rstrip('/')}/api/aterkalla",
            headers=_huvuden(),
            json={"referens": quote.quote_no, "orsak": "Återkallad i Borrjournal"},
        )
        r.raise_for_status()
    return r.json()
