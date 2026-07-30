from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import PushSubscription, User
from ..security import current_user, log_action, require_admin
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


# ---------------- e-post ----------------
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
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
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
