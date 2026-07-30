from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db
from .models import AuditLog, User

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


async def current_user(
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
    return user


def require_write(user: User = Depends(current_user)) -> User:
    if user.role == "lasare":
        raise HTTPException(status_code=403, detail="Ditt konto har bara läsrättighet")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Kräver administratör")
    return user
