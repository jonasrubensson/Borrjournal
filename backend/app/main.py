import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import scheduler
from .config import settings
from .db import SessionLocal, init_db
from .models import User
from .routers import (
    auth,
    backups,
    customers,
    files,
    journal,
    nearby,
    reminders,
    search,
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
    task = asyncio.create_task(scheduler.loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Borrjournal", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(journal.router)
app.include_router(files.router)
app.include_router(search.router)
app.include_router(reminders.router)
app.include_router(backups.router)
app.include_router(settings_router.router)
app.include_router(nearby.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Okänd endpoint")
        candidate = os.path.join(FRONTEND_DIR, path)
        if path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
