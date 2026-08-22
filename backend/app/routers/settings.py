from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import iso_utc
from ..models import PushSubscription, User
from ..security import current_user, log_action, require_admin, require_write
from ..services.notify import (
    DEFAULT_SMTP,
    SMTP_KEY,
    ensure_vapid,
    get_setting,
    public_smtp,
    save_setting,
    send_email,
    send_push,
)

router = APIRouter(prefix="/api/notifications", tags=["notiser"])
events_router = APIRouter(prefix="/api/events", tags=["systemhändelser"])


# ---------------- e-post ----------------
@router.post("/signering/test")
async def signering_test(
    payload: dict,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Provkör hela uppsättningen och mejlar ett testmeddelande."""
    from ..services import signering as sign

    try:
        return await sign.sjalvtest(db, (payload.get("till") or "").strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:250]) from exc


@router.get("/signering")
async def signering_status(
    _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Är signeringstjänsten igång, och når vi den?"""
    from ..config import settings as _s
    from ..services import signering as sign

    ut = {
        "aktiverad": sign.aktiverad(),
        "url": _s.signering_url,
        "nyckel_satt": bool(_s.signering_nyckel),
        "nyckel_lang_nog": len(_s.signering_nyckel or "") >= 24,
        "nar": None,
        "fel": "",
    }
    if not ut["aktiverad"]:
        return ut

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{_s.signering_url.rstrip('/')}/api/halsa")
            ut["nar"] = r.status_code == 200
            if not ut["nar"]:
                ut["fel"] = f"Tjänsten svarade {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        ut["nar"] = False
        ut["fel"] = str(exc)[:200]
    return ut


@router.get("/email/leverantorer")
async def leverantorer(_: User = Depends(current_user)):
    """Färdiga inställningar, så att ingen behöver leta upp portnummer."""
    from ..services.notify import LEVERANTORER

    return {"leverantorer": LEVERANTORER}


@router.get("/email")
async def read_email(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return public_smtp(await get_setting(db, SMTP_KEY, DEFAULT_SMTP))


@router.put("/email")
async def write_email(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    conf = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    for key in ("enabled", "host", "port", "security", "username", "sender", "recipients"):
        if key in payload:
            conf[key] = payload[key]
    # Tomt lösenordsfält betyder "behåll det som redan är sparat"
    if payload.get("password"):
        conf["password"] = payload["password"]
    if isinstance(conf.get("recipients"), str):
        conf["recipients"] = [x.strip() for x in conf["recipients"].split(",") if x.strip()]
    await save_setting(db, SMTP_KEY, conf)

    # Signeringstjänsten behöver samma uppgifter för att kunna skicka
    # engångskoder. Skicka dem direkt så att det inte glöms bort.
    try:
        from ..services import signering as _sign

        if _sign.aktiverad():
            await _sign.synka_installningar(db)
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] kunde inte synka mejl till signeringen: {exc}")
    await log_action(db, "SMTP_UPDATE", actor=user.username, request=request, detail=conf["host"])
    return public_smtp(conf)


@router.post("/email/test")
async def test_email(
    payload: dict | None = None,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    conf = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    to = None
    if payload and payload.get("to"):
        to = [payload["to"]]
    try:
        await send_email(
            {**conf, "enabled": True},
            "Borrjournal: testmeddelande",
            "Om du läser det här fungerar e-postutskicket.\n\n"
            f"Skickat av {user.full_name or user.username}.\n",
            to,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Utskicket misslyckades: {exc}") from exc
    return {"ok": True, "sent_to": to or conf.get("recipients", [])}


# ---------------- webbpush ----------------
@router.get("/push/key")
async def push_key(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    conf = await ensure_vapid(db)
    return {"public_key": conf["public"]}


@router.post("/push/subscribe", status_code=201)
async def subscribe(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(status_code=400, detail="Ofullständig prenumeration")

    existing = (
        await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    ).scalar_one_or_none()
    if existing:
        existing.user_id = user.id
        existing.p256dh = keys["p256dh"]
        existing.auth = keys["auth"]
        existing.failures = 0
    else:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=endpoint,
                p256dh=keys["p256dh"],
                auth=keys["auth"],
                user_agent=request.headers.get("user-agent", "")[:250],
            )
        )
    await db.commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
async def unsubscribe(
    payload: dict, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    endpoint = payload.get("endpoint", "")
    row = (
        await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    ).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return {"ok": True}


@router.get("/push/status")
async def push_status(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))
    ).scalars().all()
    return {
        "devices": [
            {
                "id": r.id,
                "user_agent": r.user_agent,
                "created_at": iso_utc(r.created_at) if r.created_at else None,
                "last_used_at": iso_utc(r.last_used_at) if r.last_used_at else None,
            }
            for r in rows
        ]
    }


@router.post("/push/test")
async def test_push(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    sent = await send_push(
        db,
        {
            "title": "Borrjournal",
            "body": "Testnotis. Notiser fungerar på den här enheten.",
            "url": "/#/paminnelser",
            "tag": "test",
        },
        [user.id],
    )
    if not sent:
        raise HTTPException(
            status_code=400,
            detail="Ingen enhet tog emot notisen. Slå på notiser på enheten först.",
        )
    return {"sent": sent}


@events_router.get("")
async def list_events(
    level: str | None = None,
    only_open: bool = False,
    limit: int = 60,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vad som gått fel i bakgrunden."""
    from ..models import SystemEvent

    stmt = select(SystemEvent).order_by(SystemEvent.at.desc()).limit(min(limit, 200))
    if level:
        stmt = stmt.where(SystemEvent.level == level)
    if only_open:
        stmt = stmt.where(SystemEvent.acknowledged.is_(False))
    rader = (await db.execute(stmt)).scalars().all()

    oppna = (
        await db.execute(
            select(func.count())
            .select_from(SystemEvent)
            .where(SystemEvent.acknowledged.is_(False))
        )
    ).scalar() or 0

    return {
        "open": oppna,
        "events": [
            {
                "id": r.id,
                "level": r.level,
                "source": r.source,
                "message": r.message,
                "detail": r.detail,
                "object_type": r.object_type,
                "object_id": r.object_id,
                "reference": r.reference,
                "at": iso_utc(r.at),
                "acknowledged": r.acknowledged,
            }
            for r in rader
        ],
    }


@events_router.post("/acknowledge")
async def acknowledge_events(
    payload: dict | None = None,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Kvitterar händelser som är hanterade."""
    from ..models import SystemEvent

    ider = (payload or {}).get("ids")
    stmt = select(SystemEvent).where(SystemEvent.acknowledged.is_(False))
    if ider:
        stmt = stmt.where(SystemEvent.id.in_(ider))
    rader = (await db.execute(stmt)).scalars().all()
    for r in rader:
        r.acknowledged = True
    await db.commit()
    return {"acknowledged": len(rader)}

