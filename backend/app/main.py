import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
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
app.include_router(nearby.router)
app.include_router(visits.router)
app.include_router(articles.router)
app.include_router(orders.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/version")
async def version():
    return {"version": APP_VERSION}


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
