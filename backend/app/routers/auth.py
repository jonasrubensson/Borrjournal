from datetime import datetime, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import LoginIn, UserIn, iso_utc, user_out
from ..security import (
    create_token,
    current_user,
    hash_password,
    log_action,
    needs_totp_setup,
    require_admin,
    totp_globally_required,
    verify_password,
)

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login")
async def login(payload: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    user = (
        await db.execute(
            select(User).where(
                (User.username == payload.username) | (User.email == payload.username)
            )
        )
    ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        await log_action(
            db, "LOGIN_FAIL", actor=payload.username, request=request, detail="fel användare eller lösenord"
        )
        raise HTTPException(status_code=401, detail="Fel användarnamn eller lösenord")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Kontot är avstängt")

    if user.totp_enabled and user.totp_secret:
        if not payload.totp_code:
            # Klienten vet då att den ska visa fältet för engångskod
            raise HTTPException(status_code=428, detail="Engångskod krävs")
        if not pyotp.TOTP(user.totp_secret).verify(payload.totp_code, valid_window=1):
            await log_action(db, "LOGIN_FAIL", actor=user.username, request=request, detail="fel engångskod")
            raise HTTPException(status_code=401, detail="Fel engångskod")

    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await log_action(db, "LOGIN", actor=user.username, request=request)
    return {
        "token": create_token(user),
        "user": user_out(user),
        "totp_setup_required": await needs_totp_setup(db, user),
    }


@router.get("/me")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    data = user_out(user)
    data["totp_setup_required"] = await needs_totp_setup(db, user)
    data["totp_required"] = user.totp_required or await totp_globally_required(db)
    return data


@router.post("/me/password")
async def change_password(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(payload.get("current_password", ""), user.hashed_password):
        raise HTTPException(status_code=401, detail="Nuvarande lösenord stämmer inte")
    new = payload.get("new_password", "")
    if len(new) < 10:
        raise HTTPException(status_code=400, detail="Nytt lösenord måste vara minst 10 tecken")
    user.hashed_password = hash_password(new)
    await db.commit()
    await log_action(db, "PASSWORD_CHANGE", actor=user.username, request=request)
    return {"ok": True}


@router.post("/me/totp/start")
async def totp_start(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    secret = pyotp.random_base32()
    user.totp_secret = secret
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="Borrjournal")
    return {"secret": secret, "uri": uri}


@router.get("/me/totp/qr")
async def totp_qr(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """QR-kod att skanna med Google Authenticator, Aegis, 1Password eller liknande."""
    import io

    import qrcode
    from fastapi.responses import StreamingResponse

    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="Starta tvåfaktor först")
    uri = pyotp.TOTP(user.totp_secret).provisioning_uri(
        name=user.username, issuer_name="Borrjournal"
    )
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.post("/me/totp/disable")
async def totp_disable(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kräver lösenord, annars räcker en obevakad skärm för att slå av skyddet."""
    if not verify_password(payload.get("password", ""), user.hashed_password):
        raise HTTPException(status_code=401, detail="Fel lösenord")
    if user.totp_required or await totp_globally_required(db):
        raise HTTPException(
            status_code=403,
            detail="Tvåfaktor är obligatorisk för ditt konto och kan inte stängas av.",
        )
    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()
    await log_action(db, "TOTP_DISABLED", actor=user.username, request=request)
    return {"ok": True}


@router.post("/me/totp/confirm")
async def totp_confirm(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.totp_secret or not pyotp.TOTP(user.totp_secret).verify(
        payload.get("code", ""), valid_window=1
    ):
        raise HTTPException(status_code=400, detail="Koden stämmer inte, försök igen")
    user.totp_enabled = True
    await db.commit()
    await log_action(db, "TOTP_ENABLED", actor=user.username, request=request)
    return {"ok": True}


# ---------- användaradministration ----------
@router.get("/users")
async def list_users(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(User).order_by(User.username))).scalars().all()
    return [user_out(u) for u in users]


@router.post("/users", status_code=201)
async def create_user(
    payload: UserIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.role not in {"admin", "tekniker", "lasare"}:
        raise HTTPException(status_code=400, detail="Okänd roll")
    exists = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="Användarnamnet finns redan")
    if len(payload.password) < 10:
        raise HTTPException(status_code=400, detail="Lösenordet måste vara minst 10 tecken")

    user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name or payload.username,
        role=payload.role,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await log_action(
        db, "USER_CREATE", actor=admin.username, object_type="user", object_id=user.id, request=request
    )
    return user_out(user)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    payload: dict,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Användaren finns inte")

    # Byt användarnamn. Inloggningen bygger på id, så gamla sessioner överlever.
    if payload.get("username") and payload["username"] != user.username:
        nytt = payload["username"].strip()
        if len(nytt) < 3:
            raise HTTPException(status_code=400, detail="Användarnamnet måste vara minst 3 tecken")
        upptaget = (
            await db.execute(select(User.id).where(User.username == nytt))
        ).first()
        if upptaget:
            raise HTTPException(status_code=409, detail="Användarnamnet är upptaget")
        user.username = nytt

    if "role" in payload and payload["role"] not in {"admin", "tekniker", "lasare"}:
        raise HTTPException(status_code=400, detail="Okänd roll")

    # En administratör får inte stänga av eller degradera sig själv och bli utelåst
    if user.id == admin.id:
        if payload.get("is_active") is False:
            raise HTTPException(status_code=400, detail="Du kan inte stänga av ditt eget konto")
        if payload.get("role") and payload["role"] != "admin":
            raise HTTPException(status_code=400, detail="Du kan inte ta bort din egen adminroll")

    for field in ("full_name", "email", "role", "is_active", "totp_required"):
        if field in payload:
            setattr(user, field, payload[field])
    if payload.get("new_password"):
        if len(payload["new_password"]) < 10:
            raise HTTPException(status_code=400, detail="Lösenordet måste vara minst 10 tecken")
        user.hashed_password = hash_password(payload["new_password"])
    if payload.get("reset_totp"):
        user.totp_enabled = False
        user.totp_secret = None
    await db.commit()
    await log_action(
        db, "USER_UPDATE", actor=admin.username, object_type="user", object_id=user.id, request=request
    )
    return user_out(user)


@router.get("/security")
async def read_security(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from ..services.notify import get_setting

    return await get_setting(db, "security", {"require_totp_all": False})


@router.put("/security")
async def write_security(
    payload: dict,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from ..services.notify import get_setting, save_setting

    conf = await get_setting(db, "security", {"require_totp_all": False})
    if "require_totp_all" in payload:
        conf["require_totp_all"] = bool(payload["require_totp_all"])
    await save_setting(db, "security", conf)
    await log_action(
        db,
        "SECURITY_UPDATE",
        actor=admin.username,
        request=request,
        detail=f"kräv tvåfaktor för alla: {conf['require_totp_all']}",
    )
    return conf


@router.get("/audit")
async def audit(
    limit: int = 100, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    from ..models import AuditLog

    rows = (
        await db.execute(select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, 500)))
    ).scalars().all()
    return [
        {
            "at": iso_utc(r.at) if r.at else None,
            "actor": r.actor,
            "action": r.action,
            "object_type": r.object_type,
            "object_id": r.object_id,
            "ip_address": r.ip_address,
            "detail": r.detail,
        }
        for r in rows
    ]
