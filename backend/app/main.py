import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select

from . import scheduler
from .config import settings
from .version import APP_VERSION
from .db import SessionLocal, init_db
from .models import User
from .routers import (
    articles,
    auth,
    backups,
    customers,
    files,
    journal,
    nearby,
    orders,
    reminders,
    search,
    visits,
)
from .routers import settings as settings_router
from .security import hash_password

FRONTEND_DIR = os.getenv("FRONTEND_DIR", "/app/frontend")


async def bootstrap() -> None:
    """Skapar första administratören om användartabellen är tom."""
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
        if count:
            return
        admin = User(
            username=settings.bootstrap_admin,
            full_name="Administratör",
            role="admin",
            notify_scope="alla",
            hashed_password=hash_password(settings.bootstrap_password),
        )
        db.add(admin)
        await db.commit()
        print(
            f"[borrjournal] Administratör '{settings.bootstrap_admin}' skapad. "
            "Byt lösenord vid första inloggningen."
        )

        if settings.seed_demo:
            from .seed import seed_demo

            await seed_demo(db)
            print("[borrjournal] Demodata inlagd.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.join(settings.data_dir, "files"), exist_ok=True)
    os.makedirs(os.path.join(settings.data_dir, "thumbs"), exist_ok=True)
    await init_db()
    await bootstrap()

    # Påminnelser ska finnas direkt, inte först när nattens genomsökning kört.
    try:
        from .db import SessionLocal as _S
        from .services.reminders import generate_auto

        async with _S() as db:
            created = await generate_auto(db)
            from .services.reminders import (
                backfill_remind_at,
                generate_business,
                stang_inaktuella,
            )

            created += await generate_business(db)
            await stang_inaktuella(db)
            fyllda = await backfill_remind_at(db)
            if created or fyllda:
                print(
                    f"[borrjournal] {created} påminnelser genererade, "
                    f"{fyllda} fick tidpunkt vid start"
                )
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] kunde inte generera påminnelser vid start: {exc}")
    task = asyncio.create_task(scheduler.loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Borrjournal", version=APP_VERSION, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(journal.router)
app.include_router(files.router)
app.include_router(search.router)
app.include_router(reminders.router)
app.include_router(backups.router)
app.include_router(settings_router.router)
app.include_router(settings_router.events_router)
app.include_router(nearby.router)
app.include_router(visits.router)
app.include_router(articles.router)
app.include_router(orders.router)


@app.exception_handler(IntegrityError)
async def krock_i_databasen(request, exc):
    """Ett brutet unikhetskrav är nästan alltid två samtidiga skrivningar.

    Användaren ska få veta att det går att försöka igen, inte ett rått 500.
    """
    from fastapi.responses import JSONResponse

    text = str(getattr(exc, "orig", exc))
    print(f"[borrjournal] databaskrock vid {request.method} {request.url.path}: {text}", flush=True)

    if "UNIQUE constraint" in text or "duplicate key" in text:
        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    "Två sparningar krockade. Försök igen, uppgifterna finns kvar i formuläret."
                )
            },
        )
    return JSONResponse(
        status_code=400,
        content={"detail": "Uppgifterna gick inte att spara: " + text[:200]},
    )


@app.exception_handler(Exception)
async def ohanterat_fel(request, exc):
    """Loggar hela stackspåret och ger användaren något att hänvisa till.

    Utan detta blir ett fel bara "500" i webbläsaren och en rad i loggen som är
    svår att koppla ihop med det man just gjorde.
    """
    import traceback
    import uuid as _uuid

    from fastapi.responses import JSONResponse

    referens = str(_uuid.uuid4())[:8]
    spar = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(
        f"\n[borrjournal] FEL {referens} vid {request.method} {request.url.path}\n{spar}",
        flush=True,
    )
    # Skriv även in det i appen, så att felet går att hitta utan serveråtkomst
    try:
        from .db import SessionLocal
        from .services import events

        async with SessionLocal() as db:
            await events.logga(
                db,
                level="fel",
                source=request.url.path[:40],
                message=f"{type(exc).__name__}: {exc}"[:500],
                detail=spar,
                reference=referens,
            )
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                f"Något gick fel i servern (referens {referens}). "
                "Felet finns i loggen: docker compose logs app | grep " + referens
            ),
            "reference": referens,
        },
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


def las_ui_version() -> str:
    """Läser versionen ur app.js på disk.

    Gränssnittet monteras in som en katalog och byggs inte in i imagen. Kopierar
    man bara backendfilerna blir delarna osynkade, och det märks först när någon
    använder appen. Servern kan se båda och säga till direkt.
    """
    import re as _re

    try:
        with open(os.path.join(FRONTEND_DIR, "app.js"), encoding="utf-8") as fh:
            borjan = fh.read(4000)
    except OSError:
        return ""
    traff = _re.search(r"UI_VERSION\s*=\s*[\"']([^\"']+)", borjan)
    return traff.group(1) if traff else ""


@app.get("/api/version")
async def version():
    ui = las_ui_version()
    return {
        "version": APP_VERSION,
        "ui_version": ui,
        "in_sync": (ui == APP_VERSION) if ui else None,
    }


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    def _las_index() -> str:
        """Läser index.html och stämplar versionsnumret på tillgångarna.

        Utan versionsstämpel kan webbläsaren servera gammal app.js efter en
        uppdatering, eftersom den har egna regler för hur länge en fil utan
        cache-headers får återanvändas. Med ?v=<version> blir det en ny adress
        varje gång versionen höjs, och då hämtas filen garanterat om.
        """
        with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for fil in ("app.js", "styles.css", "manifest.json"):
            html = html.replace(f"/static/{fil}", f"/static/{fil}?v={APP_VERSION}")
        return html

    INGEN_CACHE = {
        "Cache-Control": "no-cache, must-revalidate",
        "Pragma": "no-cache",
    }

    @app.get("/")
    async def index():
        return HTMLResponse(_las_index(), headers=INGEN_CACHE)

    @app.get("/sw.js")
    async def service_worker():
        # Service workern måste alltid hämtas färsk, annars kan en gammal
        # version fortsätta styra sidan efter en uppdatering.
        sokvag = os.path.join(FRONTEND_DIR, "sw.js")
        with open(sokvag, encoding="utf-8") as fh:
            kod = fh.read()
        kod = kod.replace("__VERSION__", APP_VERSION)
        return Response(
            kod,
            media_type="text/javascript",
            headers={**INGEN_CACHE, "Service-Worker-Allowed": "/"},
        )

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Okänd endpoint")
        candidate = os.path.join(FRONTEND_DIR, path)
        if path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return HTMLResponse(_las_index(), headers=INGEN_CACHE)


@app.middleware("http")
async def cache_headers(request, call_next):
    """Versionsstämplade tillgångar får cachas länge, resten aldrig utan kontroll."""
    svar = await call_next(request)
    vag = request.url.path
    if vag.startswith("/static/"):
        if request.url.query.startswith("v="):
            svar.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            svar.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif vag.startswith("/api/"):
        svar.headers.setdefault("Cache-Control", "no-store")
    return svar
