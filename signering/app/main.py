"""Signeringstjänsten.

Publik, men vet ingenting om kunder, anläggningar eller journaler. Den tar emot
ett dokument, visar det för en mottagare som styrkt tillgången till sin
e-postadress, och lämnar tillbaka resultatet när Borrjournal frågar efter det.

Borrjournal öppnar aldrig någon väg inåt. All trafik mellan systemen går ut
från Borrjournal, precis som e-postutskicken.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from . import audit, bevis
from .models import Base, Engangskod, Handelse, Installning, Signering

DATABAS = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////data/signering.db")
DELAD_NYCKEL = os.getenv("SHARED_SECRET", "")
BAS_URL = os.getenv("PUBLIC_URL", "https://signera.dinfirma.se").rstrip("/")
TSA_URL = os.getenv("TSA_URL", "")
KOD_GILTIG_MINUTER = int(os.getenv("OTP_MINUTES", "15"))
MAX_KODFORSOK = int(os.getenv("OTP_MAX_TRIES", "5"))
STADA_EFTER_DAGAR = int(os.getenv("PURGE_DAYS", "30"))

motor = create_async_engine(DATABAS, echo=False)
Session = async_sessionmaker(motor, expire_on_commit=False)


def _sql_typ(kolumn) -> str:
    try:
        return kolumn.type.compile(motor.dialect)
    except Exception:  # noqa: BLE001
        return "TEXT"


def _standardvarde(kolumn) -> str:
    standard = getattr(kolumn, "default", None)
    if standard is not None and getattr(standard, "is_scalar", False):
        v = standard.arg
        if isinstance(v, bool):
            return f" DEFAULT {1 if v else 0}"
        if isinstance(v, (int, float)):
            return f" DEFAULT {v}"
        if isinstance(v, str):
            return " DEFAULT '" + v.replace("'", "''") + "'"
    return ""


async def _lagg_till_kolumner(anslutning) -> None:
    """Lägger till kolumner som tillkommit sedan databasen skapades.

    Utan det här slutar tjänsten att fungera efter varje uppdatering som
    utökar modellen, eftersom create_all bara skapar tabeller som saknas helt
    och aldrig rör befintliga.
    """
    from sqlalchemy import inspect, text

    def befintliga(sync_anslutning):
        i = inspect(sync_anslutning)
        return {t: {c["name"] for c in i.get_columns(t)} for t in i.get_table_names()}

    finns = await anslutning.run_sync(befintliga)
    for tabell in Base.metadata.sorted_tables:
        if tabell.name not in finns:
            continue
        for kolumn in tabell.columns:
            if kolumn.name in finns[tabell.name]:
                continue
            if not kolumn.nullable and kolumn.default is None and not kolumn.primary_key:
                print(
                    f"[signering] hoppar över {tabell.name}.{kolumn.name}: "
                    "saknar standardvärde",
                    flush=True,
                )
                continue
            await anslutning.execute(
                text(
                    f'ALTER TABLE "{tabell.name}" ADD COLUMN "{kolumn.name}" '
                    f"{_sql_typ(kolumn)}{_standardvarde(kolumn)}"
                )
            )
            print(f"[signering] la till kolumn {tabell.name}.{kolumn.name}", flush=True)


@asynccontextmanager
async def livscykel(app: FastAPI):
    if not DELAD_NYCKEL or len(DELAD_NYCKEL) < 24:
        raise RuntimeError(
            "SHARED_SECRET saknas eller är för kort. Sätt minst 24 tecken, "
            "samma värde som i Borrjournal."
        )
    async with motor.begin() as anslutning:
        await anslutning.run_sync(Base.metadata.create_all)
        await _lagg_till_kolumner(anslutning)
    if not os.getenv("PUBLIC_URL"):
        print(
            "[signering] VARNING: PUBLIC_URL är inte satt. Länkarna som mejlas till "
            f"kunder pekar på {BAS_URL} och fungerar då inte. Sätt SIGNERING_URL_PUBLIK "
            "i .env till den adress kunden ska nå.",
            flush=True,
        )
    print(f"[signering] igång, länkar pekar på {BAS_URL}", flush=True)
    yield


app = FastAPI(title="Signering", lifespan=livscykel, docs_url=None, redoc_url=None)


async def db_session() -> AsyncSession:
    async with Session() as session:
        yield session


def klient_ip(request: Request) -> str:
    for huvud in ("x-forwarded-for", "x-real-ip"):
        varde = request.headers.get(huvud)
        if varde:
            return varde.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def hasha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def kontrollera_nyckel(x_delad_nyckel: str = Header(default="")) -> None:
    """Skyddar de vägar bara Borrjournal använder."""
    if not secrets.compare_digest(x_delad_nyckel, DELAD_NYCKEL):
        raise HTTPException(status_code=401, detail="Fel nyckel")


async def hamta(db: AsyncSession, token: str) -> Signering:
    post = (
        await db.execute(select(Signering).where(Signering.token_hash == hasha(token)))
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Länken finns inte")
    giltig = post.giltig_till
    if giltig.tzinfo is None:
        giltig = giltig.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > giltig and post.status not in ("signerad", "avbojd"):
        raise HTTPException(status_code=410, detail="Länken har gått ut")
    return post


@app.middleware("http")
async def sakerhet(request: Request, call_next):
    svar = await call_next(request)
    svar.headers.setdefault("X-Content-Type-Options", "nosniff")
    svar.headers.setdefault("Referrer-Policy", "no-referrer")
    # DENY skulle blockera vår egen iframe med dokumentet, även från samma
    # ursprung. SAMEORIGIN skyddar mot att andra ramar in sidan, men låter oss
    # visa PDF:en för kunden.
    if (svar.headers.get("content-type") or "").startswith("text/html"):
        svar.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        svar.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
    svar.headers.setdefault("Cache-Control", "no-store")
    return svar


# ---------------------------------------------------------------- Borrjournal
class NyIn(BaseModel):
    referens: str
    rubrik: str = ""
    avsandare: str = ""
    belopp: float = 0.0
    belopp_text: str = ""
    mottagare_epost: str
    mottagare_namn: str = ""
    pdf_base64: str
    giltig_dagar: int = 30
    text_sida: str = ""
    text_godkann: str = ""
    text_bevis: str = ""


@app.post("/api/ny", dependencies=[Depends(kontrollera_nyckel)])
async def ny(payload: NyIn, db: AsyncSession = Depends(db_session)):
    """Tar emot ett dokument från Borrjournal och skapar en signeringslänk."""
    try:
        pdf = base64.b64decode(payload.pdf_base64)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Kunde inte läsa PDF:en") from None
    if not pdf.startswith(b"%PDF") or len(pdf) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Ogiltig eller för stor PDF")
    if "@" not in payload.mottagare_epost:
        raise HTTPException(status_code=400, detail="Ogiltig e-postadress")

    token = secrets.token_urlsafe(32)
    post = Signering(
        token_hash=hasha(token),
        referens=payload.referens[:40],
        rubrik=payload.rubrik[:200],
        avsandare=payload.avsandare[:200],
        belopp=payload.belopp,
        belopp_text=payload.belopp_text[:60],
        mottagare_epost=payload.mottagare_epost.strip()[:200],
        mottagare_namn=payload.mottagare_namn[:200],
        pdf=pdf,
        pdf_hash=bevis.hasha(pdf),
        text_sida=(payload.text_sida or "")[:4000],
        text_godkann=(payload.text_godkann or "")[:600],
        text_bevis=(payload.text_bevis or "")[:4000],
        giltig_till=datetime.now(timezone.utc)
        + timedelta(days=max(1, min(180, payload.giltig_dagar))),
    )
    db.add(post)
    await db.flush()
    await audit.logga(
        db, post.id, "skapad", epost=post.mottagare_epost,
        beskrivning=f"Signeringslänk skapad för {post.referens}",
    )
    return {
        "id": post.id,
        "lank": f"{BAS_URL}/s/{token}",
        "giltig_till": post.giltig_till.isoformat(),
        "pdf_hash": post.pdf_hash,
    }


@app.put("/api/installningar", dependencies=[Depends(kontrollera_nyckel)])
async def spara_installningar(payload: dict, db: AsyncSession = Depends(db_session)):
    """Borrjournal skickar hit sina e-postuppgifter.

    Det gör att e-post ställs in på ett enda ställe. Vill man hellre slippa ha
    lösenordet på den publika tjänsten sätter man SMTP_HOST i miljön i stället,
    då används den och det som skickas hit ignoreras.
    """
    import json as _json

    smtp = payload.get("smtp") or {}
    if not smtp.get("host"):
        raise HTTPException(status_code=400, detail="Saknar e-postserver")

    befintlig = (
        await db.execute(select(Installning).where(Installning.nyckel == "smtp"))
    ).scalar_one_or_none()
    varde = _json.dumps(
        {
            "host": smtp.get("host", ""),
            "port": int(smtp.get("port") or 587),
            "username": smtp.get("username", ""),
            "password": smtp.get("password", ""),
            "sender": smtp.get("sender", ""),
            "security": smtp.get("security", "starttls"),
        }
    )
    if befintlig:
        befintlig.varde = varde
        befintlig.uppdaterad = datetime.now(timezone.utc)
    else:
        db.add(Installning(nyckel="smtp", varde=varde))
    await db.commit()
    return {"sparat": True, "anvands": not bool(os.getenv("SMTP_HOST"))}


@app.get("/api/sjalvtest", dependencies=[Depends(kontrollera_nyckel)])
async def sjalvtest(till: str = "", db: AsyncSession = Depends(db_session)):
    """Kontrollerar att tjänsten kan göra sitt jobb, innan någon kund berörs."""
    konf = await _mejlkonf(db)
    ut = {
        "publik_url": BAS_URL,
        "publik_url_satt": bool(os.getenv("PUBLIC_URL")),
        "mejl_konfigurerat": bool(konf.get("host")),
        "mejl_kalla": konf.get("kalla", ""),
        "mejl_server": konf.get("host", ""),
        "mejl_fungerar": None,
        "mejl_fel": "",
        "varningar": [],
    }
    if not ut["publik_url_satt"]:
        ut["varningar"].append(
            "PUBLIC_URL är inte satt. Länkarna som mejlas till kunder pekar då på "
            f"{BAS_URL}, vilket inte fungerar. Sätt SIGNERING_URL_PUBLIK i .env."
        )
    elif BAS_URL.startswith("http://"):
        ut["varningar"].append(
            "Adressen är okrypterad http. Det duger för test på egen server, men "
            "använd https innan riktiga kunder får länkar."
        )
    if till:
        ok_, fel = await _skicka_epost(
            db, till, "Testmeddelande från signeringstjänsten",
            "Det här är ett test. Kommer det fram fungerar utskicken.\n",
        )
        ut["mejl_fungerar"] = ok_
        ut["mejl_fel"] = fel
    return ut


@app.get("/api/resultat", dependencies=[Depends(kontrollera_nyckel)])
async def resultat(db: AsyncSession = Depends(db_session)):
    """Borrjournal frågar: har något hänt? Bara utgående, aldrig push."""
    klara = (
        await db.execute(
            select(Signering).where(
                Signering.status.in_(["signerad", "avbojd"]),
                Signering.hamtad.is_(False),
            )
        )
    ).scalars().all()

    ut = []
    for s in klara:
        kontroll = await audit.kontrollera_kedja(db, s.id)
        ut.append(
            {
                "id": s.id,
                "referens": s.referens,
                "status": s.status,
                "avbojd_orsak": s.avbojd_orsak,
                "mottagare_epost": s.mottagare_epost,
                "signerad_at": s.signerad_at.isoformat() if s.signerad_at else None,
                "pdf_hash": s.pdf_hash,
                "signerad_pdf_hash": (
                    bevis.hasha(s.signerad_pdf) if s.signerad_pdf else ""
                ),
                "kedja_hel": kontroll["hel"],
                "signerad_pdf_base64": (
                    base64.b64encode(s.signerad_pdf).decode() if s.signerad_pdf else None
                ),
                "original_pdf_base64": (
                    base64.b64encode(s.pdf).decode() if s.pdf else None
                ),
                "handelser": [
                    {
                        "nr": h.lopnummer,
                        "typ": h.typ,
                        "beskrivning": h.beskrivning,
                        "ip": h.ip,
                        "webblasare": h.webblasare,
                        "at": (
                            h.at if h.at.tzinfo else h.at.replace(tzinfo=timezone.utc)
                        ).isoformat(),
                    }
                    for h in await audit.kedja(db, s.id)
                ],
            }
        )
    return {"antal": len(ut), "poster": ut}


@app.post("/api/kvittera", dependencies=[Depends(kontrollera_nyckel)])
async def kvittera(payload: dict, db: AsyncSession = Depends(db_session)):
    """Borrjournal bekräftar att resultatet är sparat hos dem.

    Först då raderas dokumentet här. Tjänsten ska inte vara ett arkiv.
    """
    ider = payload.get("ids") or []
    poster = (
        await db.execute(select(Signering).where(Signering.id.in_(ider)))
    ).scalars().all()
    for p in poster:
        p.hamtad = True
        p.pdf = b""
        p.signerad_pdf = None
    await db.commit()
    return {"kvitterade": len(poster)}


@app.post("/api/status", dependencies=[Depends(kontrollera_nyckel)])
async def status(payload: dict, db: AsyncSession = Depends(db_session)):
    """Var i processen ligger ett dokument som ännu inte är klart?"""
    poster = (
        await db.execute(
            select(Signering).where(Signering.referens.in_(payload.get("referenser") or []))
        )
    ).scalars().all()
    return {
        p.referens: {
            "status": p.status,
            "giltig_till": p.giltig_till.isoformat(),
            "handelser": len(await audit.kedja(db, p.id)),
        }
        for p in poster
    }


@app.post("/api/stada", dependencies=[Depends(kontrollera_nyckel)])
async def stada(db: AsyncSession = Depends(db_session)):
    """Tar bort gammalt. Ju mindre som ligger här, desto mindre kan läcka."""
    grans = datetime.now(timezone.utc) - timedelta(days=STADA_EFTER_DAGAR)
    gamla = (
        await db.execute(
            select(Signering).where(Signering.hamtad.is_(True), Signering.skapad < grans)
        )
    ).scalars().all()
    for g in gamla:
        await db.execute(delete(Handelse).where(Handelse.signering_id == g.id))
        await db.execute(delete(Engangskod).where(Engangskod.signering_id == g.id))
        await db.delete(g)
    # Utgångna som aldrig signerades
    utgangna = (
        await db.execute(
            select(Signering).where(
                Signering.giltig_till < grans, Signering.status.notin_(["signerad"])
            )
        )
    ).scalars().all()
    for u in utgangna:
        u.pdf = b""
        u.status = "utgangen"
    await db.commit()
    return {"raderade": len(gamla), "utgangna": len(utgangna)}


@app.get("/api/halsa")
async def halsa():
    return {"status": "ok"}


# ------------------------------------------------------------------- kunden
async def _mejlkonf(db: AsyncSession) -> dict:
    """Uppgifterna Borrjournal skickat hit, annars miljövariabler.

    Poängen är att e-post ställs in på ett enda ställe. Miljövariablerna finns
    kvar för den som hellre håller lösenordet utanför den publika tjänsten.
    """
    if os.getenv("SMTP_HOST"):
        return {
            "host": os.getenv("SMTP_HOST", ""),
            "port": int(os.getenv("SMTP_PORT", "587")),
            "username": os.getenv("SMTP_USER", ""),
            "password": os.getenv("SMTP_PASSWORD", ""),
            "sender": os.getenv("SMTP_SENDER", ""),
            "security": os.getenv("SMTP_SECURITY", "starttls"),
            "kalla": "miljövariabler",
        }
    post = (
        await db.execute(select(Installning).where(Installning.nyckel == "smtp"))
    ).scalar_one_or_none()
    if post and post.varde:
        import json as _json

        konf = _json.loads(post.varde)
        konf["kalla"] = "Borrjournal"
        return konf
    return {}


async def _skicka_epost(
    db: AsyncSession, till: str, amne: str, text: str, bilagor: list | None = None
) -> tuple[bool, str]:
    """Skickar med SMTP. Returnerar även orsaken när det inte gick."""
    import aiosmtplib
    from email.message import EmailMessage

    konf = await _mejlkonf(db)
    if not konf.get("host"):
        return False, (
            "Inga e-postuppgifter. Spara e-postinställningarna i Borrjournal under "
            "Inställningar, Notiser, så skickas de hit automatiskt."
        )

    meddelande = EmailMessage()
    meddelande["From"] = konf.get("sender") or "noreply@localhost"
    meddelande["To"] = till
    meddelande["Subject"] = amne
    meddelande.set_content(text)
    for namn, innehall in bilagor or []:
        meddelande.add_attachment(
            innehall, maintype="application", subtype="pdf", filename=namn
        )

    sakerhet = konf.get("security", "starttls")
    try:
        await aiosmtplib.send(
            meddelande,
            hostname=konf["host"],
            port=int(konf.get("port") or 587),
            username=konf.get("username") or None,
            password=konf.get("password") or None,
            start_tls=sakerhet == "starttls",
            use_tls=sakerhet == "tls",
            timeout=25,
        )
        return True, ""
    except Exception as exc:  # noqa: BLE001
        text_fel = str(exc)
        if "5.7.139" in text_fel or "5.7.30" in text_fel:
            text_fel += "  (SMTP AUTH avstängt för brevlådan hos Microsoft)"
        elif "535" in text_fel and "gmail" in konf["host"].lower():
            text_fel += "  (Google kräver app-lösenord vid tvåstegsverifiering)"
        print(f"[signering] e-post misslyckades: {text_fel}", flush=True)
        return False, text_fel[:250]


@app.get("/s/{token}", response_class=HTMLResponse)
async def visa(token: str, request: Request, db: AsyncSession = Depends(db_session)):
    """Sidan mottagaren möter."""
    try:
        post = await hamta(db, token)
    except HTTPException as exc:
        return HTMLResponse(_enkel_sida(exc.detail), status_code=exc.status_code)

    if post.status == "vantar":
        post.status = "oppnad"
    await audit.logga(
        db, post.id, "oppnad",
        ip=klient_ip(request), webblasare=request.headers.get("user-agent", ""),
    )
    return HTMLResponse(_signeringssida(token, post))


@app.post("/api/{token}/kod")
async def begar_kod(token: str, request: Request, db: AsyncSession = Depends(db_session)):
    """Skickar en engångskod till den adress dokumentet är ställt till."""
    post = await hamta(db, token)
    if post.status == "signerad":
        raise HTTPException(status_code=409, detail="Dokumentet är redan signerat")

    # En kod åt gången, och inte hur ofta som helst
    senaste = (
        await db.execute(
            select(Engangskod)
            .where(Engangskod.signering_id == post.id)
            .order_by(Engangskod.skapad.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if senaste is not None:
        skapad = senaste.skapad
        if skapad.tzinfo is None:
            skapad = skapad.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - skapad).total_seconds() < 60:
            raise HTTPException(
                status_code=429, detail="Vänta en minut innan du begär en ny kod"
            )

    kod = f"{secrets.randbelow(1000000):06d}"
    db.add(
        Engangskod(
            signering_id=post.id,
            kod_hash=hasha(kod),
            giltig_till=datetime.now(timezone.utc) + timedelta(minutes=KOD_GILTIG_MINUTER),
        )
    )
    await audit.logga(
        db, post.id, "kod_begard",
        ip=klient_ip(request), webblasare=request.headers.get("user-agent", ""),
        epost=post.mottagare_epost, commit=False,
    )
    await db.commit()

    skickat, orsak = await _skicka_epost(
        db,
        post.mottagare_epost,
        f"Kod för att signera {post.referens}: {kod}",
        f"Din engångskod är {kod}\n\n"
        f"Koden gäller i {KOD_GILTIG_MINUTER} minuter och används för att godkänna "
        f"{post.rubrik or post.referens}"
        + (f" på {post.belopp_text}" if post.belopp_text else "")
        + f" från {post.avsandare}.\n\n"
        "Har du inte begärt koden kan du bortse från det här meddelandet. "
        "Ingenting händer utan att koden används.\n",
    )
    if skickat:
        await audit.logga(db, post.id, "kod_skickad", epost=post.mottagare_epost)
        return {"skickad": True, "till": _maskera(post.mottagare_epost)}

    # Utan mejl kommer kunden ingenstans. Säg vad som är fel i stället för att
    # låtsas att koden är på väg.
    await audit.logga(
        db, post.id, "kod_fel", beskrivning=f"Kunde inte skicka koden: {orsak[:150]}",
        ip=klient_ip(request),
    )
    raise HTTPException(
        status_code=502,
        detail="Koden kunde inte skickas. Kontakta avsändaren. " + orsak[:150],
    )


def _maskera(epost: str) -> str:
    namn, _, doman = epost.partition("@")
    if len(namn) <= 2:
        return f"{namn[:1]}***@{doman}"
    return f"{namn[:2]}***{namn[-1]}@{doman}"


@app.post("/api/{token}/verifiera")
async def verifiera(
    token: str, payload: dict, request: Request, db: AsyncSession = Depends(db_session)
):
    post = await hamta(db, token)
    kod = str(payload.get("kod", "")).strip()

    aktuell = (
        await db.execute(
            select(Engangskod)
            .where(Engangskod.signering_id == post.id, Engangskod.anvand.is_(False))
            .order_by(Engangskod.skapad.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if aktuell is None:
        raise HTTPException(status_code=400, detail="Begär en kod först")

    giltig = aktuell.giltig_till
    if giltig.tzinfo is None:
        giltig = giltig.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > giltig:
        raise HTTPException(status_code=400, detail="Koden har gått ut, begär en ny")
    if aktuell.forsok >= MAX_KODFORSOK:
        raise HTTPException(status_code=429, detail="För många försök, begär en ny kod")

    if not secrets.compare_digest(hasha(kod), aktuell.kod_hash):
        aktuell.forsok += 1
        await audit.logga(
            db, post.id, "kod_fel", ip=klient_ip(request),
            webblasare=request.headers.get("user-agent", ""), commit=False,
            beskrivning=f"Fel engångskod, försök {aktuell.forsok} av {MAX_KODFORSOK}",
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="Fel kod")

    aktuell.anvand = True
    post.status = "verifierad" if post.status != "signerad" else post.status
    await audit.logga(
        db, post.id, "kod_ok", ip=klient_ip(request),
        webblasare=request.headers.get("user-agent", ""), epost=post.mottagare_epost,
        commit=False,
    )
    await db.commit()
    return {"ok": True}


@app.get("/api/{token}/pdf")
async def pdf(token: str, request: Request, db: AsyncSession = Depends(db_session)):
    post = await hamta(db, token)
    if post.status not in ("verifierad", "signerad"):
        raise HTTPException(status_code=403, detail="Ange engångskoden först")
    await audit.logga(
        db, post.id, "dokument_visat", ip=klient_ip(request),
        webblasare=request.headers.get("user-agent", ""), epost=post.mottagare_epost,
    )
    innehall = post.signerad_pdf or post.pdf
    return Response(
        innehall,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{post.referens}.pdf"'},
    )


@app.post("/api/{token}/signera")
async def signera(
    token: str, payload: dict, request: Request, db: AsyncSession = Depends(db_session)
):
    post = await hamta(db, token)
    if post.status == "signerad":
        raise HTTPException(status_code=409, detail="Redan signerat")
    if post.status != "verifierad":
        raise HTTPException(status_code=403, detail="Ange engångskoden först")
    if not payload.get("godkanner"):
        raise HTTPException(status_code=400, detail="Kryssa i att du godkänner")

    namnteckning = payload.get("namnteckning") or ""
    png = None
    if namnteckning.startswith("data:image/png;base64,"):
        try:
            png = base64.b64decode(namnteckning.split(",", 1)[1])
            if len(png) > 400_000:
                png = None
        except Exception:  # noqa: BLE001
            png = None

    post.status = "signerad"
    post.signerad_at = datetime.now(timezone.utc)
    post.namnteckning = "ritad" if png else ""
    await audit.logga(
        db, post.id, "signerad", ip=klient_ip(request),
        webblasare=request.headers.get("user-agent", ""), epost=post.mottagare_epost,
        beskrivning=(
            f"Godkände {post.rubrik or post.referens}"
            + (f" på {post.belopp_text}" if post.belopp_text else "")
        ),
        commit=False,
    )
    await db.commit()

    kontroll = await audit.kontrollera_kedja(db, post.id)
    stampel = await audit.tidsstampla(post.pdf, TSA_URL)
    original_namn = f"{post.referens}.pdf"
    sida = bevis.bygg_revisionssida(
        referens=post.referens,
        rubrik=post.rubrik,
        avsandare=post.avsandare,
        mottagare_epost=post.mottagare_epost,
        mottagare_namn=post.mottagare_namn,
        belopp_text=post.belopp_text,
        pdf_hash=post.pdf_hash,
        signerad_at=post.signerad_at,
        handelser=await audit.kedja(db, post.id),
        kedja_ok=kontroll["hel"],
        kedja_text=kontroll["text"],
        namnteckning_png=png,
        tidsstampel=stampel,
        egen_forklaring=post.text_bevis,
        filnamn_original=original_namn,
    )
    post.signerad_pdf = bevis.foga_ihop(
        post.pdf,
        sida,
        sidfot=(
            f"Elektroniskt signerad {post.signerad_at.astimezone().strftime('%Y-%m-%d %H:%M')} "
            f"av {post.mottagare_epost} · {post.referens} · se signeringsbevis sist"
        ),
    )
    await db.commit()

    await _skicka_epost(
        db,
        post.mottagare_epost,
        f"Kvittens: du har godkänt {post.referens}",
        f"Du godkände {post.rubrik or post.referens}"
        + (f" på {post.belopp_text}" if post.belopp_text else "")
        + f" den {post.signerad_at.astimezone().strftime('%Y-%m-%d %H:%M')}.\n\n"
        f"Godkännandet skedde från IP-adressen {klient_ip(request)}.\n\n"
        "Två filer bifogas:\n"
        f"  {original_namn} — det du godkände.\n"
        f"    SHA-256: {post.pdf_hash}\n"
        f"  {post.referens} signerad.pdf — samma handling med signeringsbevis sist.\n"
        f"    SHA-256: {bevis.hasha(post.signerad_pdf or b'')}\n\n"
        "Kontrollsummorna gör att du kan visa att filerna inte ändrats. Kontrollera med\n"
        f"  certutil -hashfile {original_namn} SHA256   (Windows)\n"
        f"  shasum -a 256 {original_namn}               (macOS och Linux)\n\n"
        "Var det inte du som godkände detta, hör av dig till "
        f"{post.avsandare or 'avsändaren'} omgående.\n\n"
        "Den signerade handlingen med fullständig logg bifogas det här meddelandet.\n",
        bilagor=[
            (original_namn, post.pdf),
            (f"{post.referens} signerad.pdf", post.signerad_pdf),
        ]
        if post.signerad_pdf
        else None,
    )
    await audit.logga(db, post.id, "kvitto_skickat", epost=post.mottagare_epost)
    return {"ok": True}


@app.post("/api/{token}/avboj")
async def avboj(
    token: str, payload: dict, request: Request, db: AsyncSession = Depends(db_session)
):
    post = await hamta(db, token)
    if post.status == "signerad":
        raise HTTPException(status_code=409, detail="Redan signerat")
    post.status = "avbojd"
    post.avbojd_orsak = str(payload.get("orsak", ""))[:255]
    await audit.logga(
        db, post.id, "avbojd", ip=klient_ip(request),
        webblasare=request.headers.get("user-agent", ""),
        beskrivning=f"Avböjde: {post.avbojd_orsak or 'ingen orsak angiven'}",
        commit=False,
    )
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------- sidan
def _enkel_sida(text: str) -> str:
    return f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signering</title>{_STIL}</head><body>
<div class="kort"><h1>Det gick inte</h1><p>{text}</p>
<p class="liten">Kontakta avsändaren så skickar de en ny länk.</p></div></body></html>"""


_STIL = """<style>
:root{--ink:#0E1F2A;--ink2:#33505E;--stone:#6B7A80;--line:#D3DBDC;--vatten:#1F7A8C;
--ok:#2E7D5B;--alert:#A6402F;--brass:#B3801F}
*{box-sizing:border-box}
body{margin:0;background:#EFF2F1;color:var(--ink);
font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.kort{max-width:660px;margin:24px auto;background:#fff;border:1px solid var(--line);
border-radius:4px;padding:22px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:22px 0 8px}
p{margin:0 0 12px}
.liten{font-size:13.5px;color:var(--stone)}
.avsandare{font-size:13.5px;color:var(--stone);margin-bottom:16px}
.belopp{font-size:26px;font-weight:600;margin:6px 0 2px}
label{display:block;font-size:13px;text-transform:uppercase;letter-spacing:.05em;
color:var(--stone);margin:14px 0 4px}
input[type=text]{width:100%;padding:12px;border:1px solid var(--line);border-radius:3px;
font-size:17px;font-family:inherit}
input.kod{letter-spacing:.4em;text-align:center;font-size:24px;font-family:ui-monospace,monospace}
button{background:var(--vatten);color:#fff;border:none;border-radius:3px;padding:13px 20px;
font:inherit;font-weight:600;cursor:pointer;min-height:48px}
button.ghost{background:none;color:var(--ink2);border:1px solid var(--line)}
.knapplank{display:inline-flex;align-items:center;justify-content:center;background:var(--vatten);
color:#fff;border-radius:3px;padding:13px 20px;font-weight:600;text-decoration:none;min-height:48px}
button:disabled{opacity:.5}
.rad{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px}
iframe{width:100%;height:44vh;min-height:260px;border:1px solid var(--line);border-radius:3px;
background:#fff}
@media (min-width:700px){iframe{height:60vh}}
.kryss{display:flex;gap:11px;align-items:flex-start;background:#F4F9FA;border:1px solid #C9DFE3;
border-radius:3px;padding:14px;margin:16px 0}
.kryss input{width:22px;height:22px;margin-top:1px;flex:none}
canvas{width:100%;height:130px;border:1px dashed var(--line);border-radius:3px;
background:#fff;touch-action:none;display:block;cursor:crosshair}
.ritruta{position:relative}
.ritruta .placeholder{position:absolute;inset:0;display:flex;align-items:center;
justify-content:center;color:var(--stone);font-size:14px;pointer-events:none}
.fel{color:var(--alert);font-size:14px;margin-top:8px}
.ok{color:var(--ok)}
.steg{display:none}.steg.pa{display:block}
.klar{text-align:center;padding:14px 0}
.klar .bock{font-size:44px;color:var(--ok)}
.info{background:#F4F7F7;border-radius:3px;padding:12px;font-size:13.5px;color:var(--ink2)}
</style>"""


def _signeringssida(token: str, post: Signering) -> str:
    redan = post.status == "signerad"
    return f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{post.rubrik or post.referens}</title>{_STIL}</head><body>
<div class="kort">
  <div class="avsandare">{post.text_sida or (post.avsandare or "Avsändare") + " har skickat ett dokument för godkännande"}</div>
  <h1>{post.rubrik or post.referens}</h1>
  {f'<div class="belopp">{post.belopp_text}</div>' if post.belopp_text else ''}
  <p class="liten">{post.referens} · giltig till {post.giltig_till.strftime("%Y-%m-%d")}</p>

  <div id="klar" class="steg {'pa' if redan else ''}">
    <div class="klar"><div class="bock">&#10003;</div>
      <h2>Tack, dokumentet är godkänt</h2>
      <p class="liten">En kvittens har skickats till din e-post.</p></div>
    <div class="info">Var det inte du som godkände detta, hör av dig till
      {post.avsandare or "avsändaren"} omgående.</div>
  </div>

  <div id="steg1" class="steg {'' if redan else 'pa'}">
    <h2>Bekräfta att det är du</h2>
    <p>Vi skickar en engångskod till <strong>{_maskera(post.mottagare_epost)}</strong>.
      Koden visar att du har tillgång till adressen dokumentet är ställt till.</p>
    <button id="skicka">Skicka kod</button>
    <div id="fel1" class="fel"></div>
  </div>

  <div id="steg2" class="steg">
    <h2>Ange koden</h2>
    <p class="liten">Kolla din e-post. Koden gäller i {KOD_GILTIG_MINUTER} minuter.</p>
    <label for="kod">Engångskod</label>
    <input id="kod" class="kod" type="text" inputmode="numeric" maxlength="6"
      autocomplete="one-time-code" placeholder="000000">
    <div class="rad"><button id="verifiera">Fortsätt</button>
      <button class="ghost" id="ny_kod">Skicka ny kod</button></div>
    <div id="fel2" class="fel"></div>
  </div>

  <div id="steg3" class="steg">
    <h2>Läs igenom dokumentet</h2>
    <div class="rad" style="margin-top:0">
      <a id="lank" class="knapplank" href="#" target="_blank" rel="noopener">Öppna dokumentet</a>
      <a id="ladda" class="liten" href="#" download>Ladda ner</a>
    </div>
    <iframe id="ram" title="Dokumentet"></iframe>
    <p class="liten" id="ramhjalp" style="margin-top:8px;display:none">
      Visas inte dokumentet här? Tryck <strong>Öppna dokumentet</strong> ovan, en del
      webbläsare i telefonen visar inte PDF direkt på sidan.</p>

    <label>Din namnteckning</label>
    <div class="ritruta">
      <canvas id="rita"></canvas>
      <div class="placeholder" id="ritahjalp">Rita här med fingret eller musen</div>
    </div>
    <div class="rad" style="margin-top:6px">
      <button class="ghost" id="rensa" style="min-height:38px;padding:7px 14px">Rensa</button>
      <span class="liten">Frivilligt, men gör handlingen tydligare.</span></div>

    <div class="kryss">
      <input type="checkbox" id="godkanner">
      <label for="godkanner" style="margin:0;text-transform:none;letter-spacing:0;
        font-size:15px;color:var(--ink)">
        {
          post.text_godkann.format(
            rubrik=post.rubrik or post.referens,
            belopp=post.belopp_text,
            referens=post.referens,
            avsandare=post.avsandare,
          )
          if post.text_godkann
          else (
            "Jag har läst dokumentet och godkänner "
            + (post.rubrik or post.referens)
            + (f" på {post.belopp_text}" if post.belopp_text else "")
            + ". Jag är införstådd med att detta är bindande."
          )
        }</label>
    </div>

    <div class="rad">
      <button id="signera" disabled>Godkänn och signera</button>
      <button class="ghost" id="avboj">Avböj</button>
    </div>
    <div id="fel3" class="fel"></div>
    <p class="liten" style="margin-top:14px">Tidpunkt, IP-adress och webbläsare sparas
      tillsammans med godkännandet och redovisas på den signerade handlingen.</p>
  </div>
</div>

<script>
const T = {token!r};
const $ = (id) => document.getElementById(id);
const visa = (id) => {{
  for (const s of document.querySelectorAll(".steg")) s.classList.remove("pa");
  $(id).classList.add("pa");
  // Ritytan måste mätas när den syns, annars blir den noll pixlar
  if (id === "steg3") requestAnimationFrame(() => storlek());
  $(id).scrollIntoView({{ block: "start", behavior: "instant" }});
}};
async function anrop(vag, kropp) {{
  const r = await fetch(`/api/${{T}}${{vag}}`, {{
    method: "POST", headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(kropp || {{}}),
  }});
  const d = await r.json().catch(() => ({{}}));
  if (!r.ok) throw new Error(d.detail || `Fel ${{r.status}}`);
  return d;
}}

$("skicka").onclick = async (e) => {{
  e.target.disabled = true; $("fel1").textContent = "";
  try {{ await anrop("/kod"); visa("steg2"); $("kod").focus(); }}
  catch (err) {{ $("fel1").textContent = err.message; }}
  e.target.disabled = false;
}};
$("ny_kod").onclick = async () => {{
  $("fel2").textContent = "";
  try {{ await anrop("/kod"); $("fel2").innerHTML = '<span class="ok">Ny kod skickad.</span>'; }}
  catch (err) {{ $("fel2").textContent = err.message; }}
}};
$("verifiera").onclick = async (e) => {{
  e.target.disabled = true; $("fel2").textContent = "";
  try {{
    await anrop("/verifiera", {{ kod: $("kod").value.trim() }});
    const vag = `/api/${{T}}/pdf`;
    $("ram").src = vag;
    $("lank").href = vag;
    $("ladda").href = vag;
    // Telefoner visar ofta inte PDF i en ram. Märks det, lyft fram knappen.
    setTimeout(() => {{
      try {{
        const h = $("ram").contentWindow;
        if (!h || !h.length) $("ramhjalp").style.display = "block";
      }} catch (_) {{ $("ramhjalp").style.display = "block"; }}
    }}, 2500);
    visa("steg3");
  }} catch (err) {{ $("fel2").textContent = err.message; }}
  e.target.disabled = false;
}};
$("kod").addEventListener("keydown", (e) => {{ if (e.key === "Enter") $("verifiera").click(); }});
$("godkanner").onchange = (e) => {{ $("signera").disabled = !e.target.checked; }};

/* namnteckning */
const c = $("rita"), ctx = c.getContext("2d");
let ritar = false, nagot = false;
/* Rutan ligger i ett dolt steg vid sidladdning. Mäts den då blir den noll
   pixlar bred och går inte att rita på. Därför mäts den när steget visas. */
function storlek() {{
  const r = c.getBoundingClientRect();
  if (!r.width) return false;
  const skala = window.devicePixelRatio || 1;
  const bild = c.width && nagot ? ctx.getImageData(0, 0, c.width, c.height) : null;
  c.width = Math.round(r.width * skala);
  c.height = Math.round(r.height * skala);
  // Nollställ först, annars multipliceras skalan för varje anrop
  ctx.setTransform(skala, 0, 0, skala, 0, 0);
  ctx.lineWidth = 2; ctx.lineCap = "round"; ctx.lineJoin = "round";
  ctx.strokeStyle = "#0E1F2A";
  if (bild) ctx.putImageData(bild, 0, 0);
  return true;
}}
window.addEventListener("resize", storlek);
function punkt(e) {{
  const r = c.getBoundingClientRect();
  const t = e.touches ? e.touches[0] : e;
  return [t.clientX - r.left, t.clientY - r.top];
}}
function ned(e) {{
  e.preventDefault();
  if (!c.width) storlek();
  ritar = true; nagot = true;
  const h = $("ritahjalp"); if (h) h.style.display = "none";
  ctx.beginPath(); ctx.moveTo(...punkt(e));
}}
function ror(e) {{ if (!ritar) return; e.preventDefault(); ctx.lineTo(...punkt(e)); ctx.stroke(); }}
function upp() {{ ritar = false; }}
c.addEventListener("pointerdown", ned); c.addEventListener("pointermove", ror);
window.addEventListener("pointerup", upp);
$("rensa").onclick = () => {{
  ctx.clearRect(0, 0, c.width, c.height);
  nagot = false;
  const h = $("ritahjalp"); if (h) h.style.display = "flex";
}};

$("signera").onclick = async (e) => {{
  e.target.disabled = true; $("fel3").textContent = "";
  try {{
    await anrop("/signera", {{
      godkanner: true,
      namnteckning: nagot ? c.toDataURL("image/png") : "",
    }});
    visa("klar");
  }} catch (err) {{ $("fel3").textContent = err.message; e.target.disabled = false; }}
}};
$("avboj").onclick = async () => {{
  const orsak = prompt("Vill du säga varför? (frivilligt)") ?? null;
  if (orsak === null) return;
  try {{ await anrop("/avboj", {{ orsak }});
    document.querySelector(".kort").innerHTML =
      "<h1>Avböjt</h1><p>Avsändaren får besked. Tack för svaret.</p>";
  }} catch (err) {{ $("fel3").textContent = err.message; }}
}};
</script></body></html>"""
