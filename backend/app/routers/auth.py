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
    from ..services import inloggning

    ip = inloggning.klient_ip(request)
    webblasare = request.headers.get("user-agent", "") if request else ""
    conf = await inloggning.installningar(db)

    sparrad, kvar = await inloggning.ar_sparrad(db, payload.username, ip)
    if sparrad:
        await inloggning.notera(
            db, username=payload.username, ip=ip, user_agent=webblasare,
            success=False, reason="spärrad",
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"För många misslyckade försök. Försök igen om "
                f"{max(1, kvar // 60)} minuter."
            ),
            headers={"Retry-After": str(max(1, kvar))},
        )

    user = (
        await db.execute(
            select(User).where(
                (User.username == payload.username) | (User.email == payload.username)
            )
        )
    ).scalar_one_or_none()

    async def misslyckat(anledning: str, status: int = 401, meddelande: str = ""):
        await inloggning.notera(
            db, username=payload.username, ip=ip, user_agent=webblasare,
            success=False, reason=anledning,
        )
        await log_action(
            db, "LOGIN_FAIL", actor=payload.username, request=request, detail=anledning
        )
        # Nådde vi gränsen med det här försöket är det värt att säga till
        nu_sparrad, _ = await inloggning.ar_sparrad(db, payload.username, ip)
        if nu_sparrad:
            await inloggning.avisera_sparr(
                db, username=payload.username, ip=ip, antal=int(conf["max_forsok"])
            )
        raise HTTPException(
            status_code=status, detail=meddelande or "Fel användarnamn eller lösenord"
        )

    if user is None or not verify_password(payload.password, user.hashed_password):
        # Samma svar oavsett om kontot finns, annars går det att kartlägga vilka
        # användarnamn som existerar
        await misslyckat("fel användare eller lösenord")

    if not user.is_active:
        await misslyckat("kontot avstängt", 403, "Kontot är avstängt")

    if user.totp_enabled and user.totp_secret:
        if not payload.totp_code:
            # Klienten vet då att den ska visa fältet för engångskod
            raise HTTPException(status_code=428, detail="Engångskod krävs")
        if not pyotp.TOTP(user.totp_secret).verify(payload.totp_code, valid_window=1):
            await misslyckat("fel engångskod", 401, "Fel engångskod")

    ny_plats = not await inloggning.kand_plats(db, user.username, ip)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await inloggning.notera(
        db, username=user.username, ip=ip, user_agent=webblasare, success=True
    )
    await log_action(db, "LOGIN", actor=user.username, request=request)
    await inloggning.avisera_inloggning(
        db, user=user, ip=ip, user_agent=webblasare, ny_plats=ny_plats
    )
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
    data["notify_scope"] = user.notify_scope
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


@router.put("/me/notify-scope")
async def set_notify_scope(
    payload: dict,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vilka påminnelser användaren vill bli meddelad om."""
    omfang = payload.get("scope")
    if omfang not in ("mina", "alla", "inga"):
        raise HTTPException(status_code=400, detail="Ange mina, alla eller inga")
    user.notify_scope = omfang
    await db.commit()
    await log_action(db, "NOTIFY_SCOPE", actor=user.username, request=request, detail=omfang)
    return {"notify_scope": omfang}


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
        # En administratör ser allt som standard, så att inget faller mellan
        # stolarna. Går att ändra på det egna kontot.
        notify_scope="alla" if payload.role == "admin" else "mina",
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

    for field in ("full_name", "email", "role", "is_active", "totp_required", "notify_scope"):
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



@router.get("/login-attempts")
async def login_attempts(
    limit: int = 60,
    only_failed: bool = False,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Vem har loggat in varifrån, och vilka försök har misslyckats."""
    from ..models import LoginAttempt
    from ..services.inloggning import installningar

    stmt = select(LoginAttempt).order_by(LoginAttempt.at.desc()).limit(min(limit, 300))
    if only_failed:
        stmt = stmt.where(LoginAttempt.success.is_(False))
    rader = (await db.execute(stmt)).scalars().all()
    return {
        "settings": await installningar(db),
        "attempts": [
            {
                "id": r.id,
                "username": r.username,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "success": r.success,
                "reason": r.reason,
                "at": iso_utc(r.at),
            }
            for r in rader
        ],
    }


@router.put("/login-settings")
async def login_settings(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from ..services.inloggning import NYCKEL, STANDARD, installningar
    from ..services.notify import save_setting

    conf = await installningar(db)
    for nyckel in STANDARD:
        if nyckel in payload:
            if isinstance(STANDARD[nyckel], bool):
                conf[nyckel] = bool(payload[nyckel])
            else:
                conf[nyckel] = max(1, min(9999, int(payload[nyckel])))
    await save_setting(db, NYCKEL, conf)
    await log_action(db, "LOGIN_SETTINGS", actor=user.username, request=request)
    return conf


@router.post("/login-attempts/clear")
async def clear_block(
    payload: dict,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Häver en spärr, till exempel när någon glömt sitt lösenord."""
    from sqlalchemy import delete as _delete

    from ..models import LoginAttempt

    villkor = []
    if payload.get("username"):
        villkor.append(LoginAttempt.username == payload["username"])
    if payload.get("ip"):
        villkor.append(LoginAttempt.ip == payload["ip"])
    if not villkor:
        raise HTTPException(status_code=400, detail="Ange användarnamn eller IP-adress")

    from sqlalchemy import or_ as _or

    resultat = await db.execute(
        _delete(LoginAttempt).where(_or(*villkor), LoginAttempt.success.is_(False))
    )
    await db.commit()
    await log_action(
        db, "LOGIN_UNBLOCK", actor=user.username, request=request,
        detail=str(payload.get("username") or payload.get("ip")),
    )
    return {"rensade": resultat.rowcount or 0}
