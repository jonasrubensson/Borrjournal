from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db
from .models import AppSetting, AuditLog, User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.token_expire_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


async def log_action(
    db: AsyncSession,
    action: str,
    *,
    actor: str = "",
    object_type: str = "",
    object_id: str = "",
    request: Request | None = None,
    detail: str = "",
) -> None:
    ip = ""
    if request is not None:
        ip = request.headers.get("x-forwarded-for", "") or (
            request.client.host if request.client else ""
        )
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            ip_address=ip.split(",")[0].strip(),
            detail=detail,
        )
    )
    await db.commit()


# Vägar som måste vara öppna även för den som är tvingad att sätta upp tvåfaktor,
# annars går det inte att sätta upp den.
TOTP_SETUP_PATHS = (
    "/api/me",
    "/api/me/totp/start",
    "/api/me/totp/qr",
    "/api/me/totp/confirm",
    "/api/me/password",
    "/api/version",
    "/api/health",
    # Nödutgång: en administratör som slår på kravet utan att själv ha tvåfaktor
    # påslagen måste kunna nå inställningen igen för att stänga av det.
    # Rutten är ändå skyddad av require_admin.
    "/api/security",
)


async def totp_globally_required(db: AsyncSession) -> bool:
    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == "security"))
    ).scalar_one_or_none()
    if row is None or not row.value:
        return False
    import json

    try:
        return bool(json.loads(row.value).get("require_totp_all"))
    except json.JSONDecodeError:
        return False


async def needs_totp_setup(db: AsyncSession, user: User) -> bool:
    if user.totp_enabled:
        return False
    return user.totp_required or await totp_globally_required(db)


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Inte inloggad")
    try:
        payload = jwt.decode(
            credentials.credentials, settings.secret_key, algorithms=[settings.algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessionen har gått ut, logga in igen")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Ogiltig session")

    user = (await db.execute(select(User).where(User.id == payload.get("sub")))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Kontot finns inte eller är avstängt")

    # Är tvåfaktor påtvingad men inte påslagen släpps bara uppsättningen igenom.
    if request.url.path not in TOTP_SETUP_PATHS and await needs_totp_setup(db, user):
        raise HTTPException(
            status_code=403,
            detail="totp_setup_required",
        )
    return user


def require_write(user: User = Depends(current_user)) -> User:
    if user.role == "lasare":
        raise HTTPException(status_code=403, detail="Ditt konto har bara läsrättighet")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Kräver administratör")
    return user
