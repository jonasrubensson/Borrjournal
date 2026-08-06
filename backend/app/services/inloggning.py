"""Inloggningsskydd: blockering och avisering.

Lösenordsgissning är det billigaste angreppet mot vilken app som helst. Skyddet
här består av tre delar:

* försök räknas per användarnamn och per IP, och båda spärras vid för många
* en lyckad inloggning från en ny plats meddelas administratörerna
* varje försök sparas, så att man kan svara på vad som hänt i efterhand

Spärren är tidsbegränsad i stället för permanent. En permanent spärr gör att
den som gissar kan låsa ute den riktiga användaren, vilket är ett angrepp i sig.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LoginAttempt, User

STANDARD = {
    "max_forsok": 6,
    "fonster_minuter": 15,
    "sparr_minuter": 15,
    "avisera_lyckade": True,
    "avisera_sparr": True,
    "spara_dagar": 90,
}
NYCKEL = "inloggning"


async def installningar(db: AsyncSession) -> dict:
    from .notify import get_setting

    return await get_setting(db, NYCKEL, STANDARD)


def klient_ip(request) -> str:
    """IP bakom en proxy.

    Nginx skickar X-Forwarded-For. Den kan förfalskas av klienten om proxyn inte
    skriver över den, så värdet duger till loggning och grov blockering, inte
    till åtkomstkontroll.
    """
    if request is None:
        return ""
    for huvud in ("x-forwarded-for", "x-real-ip"):
        varde = request.headers.get(huvud)
        if varde:
            return varde.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


async def ar_sparrad(db: AsyncSession, username: str, ip: str) -> tuple[bool, int]:
    """Är användarnamnet eller IP:n spärrad just nu? Returnerar sekunder kvar."""
    conf = await installningar(db)
    grans = datetime.now(timezone.utc) - timedelta(minutes=int(conf["fonster_minuter"]))

    for kolumn, varde in ((LoginAttempt.username, username), (LoginAttempt.ip, ip)):
        if not varde:
            continue
        rader = (
            await db.execute(
                select(LoginAttempt)
                .where(kolumn == varde, LoginAttempt.at >= grans)
                .order_by(LoginAttempt.at.desc())
                .limit(50)
            )
        ).scalars().all()

        misslyckade = []
        for r in rader:
            if r.success:
                break  # en lyckad inloggning nollställer räkningen
            misslyckade.append(r)

        if len(misslyckade) >= int(conf["max_forsok"]):
            senaste = misslyckade[0].at
            if senaste.tzinfo is None:
                senaste = senaste.replace(tzinfo=timezone.utc)
            slut = senaste + timedelta(minutes=int(conf["sparr_minuter"]))
            kvar = int((slut - datetime.now(timezone.utc)).total_seconds())
            if kvar > 0:
                return True, kvar
    return False, 0


async def notera(
    db: AsyncSession,
    *,
    username: str,
    ip: str,
    user_agent: str,
    success: bool,
    reason: str = "",
) -> LoginAttempt:
    post = LoginAttempt(
        username=(username or "")[:64],
        ip=ip,
        user_agent=(user_agent or "")[:255],
        success=success,
        reason=reason[:60],
    )
    db.add(post)
    await db.commit()
    return post


async def kand_plats(db: AsyncSession, username: str, ip: str) -> bool:
    """Har användaren loggat in från den här IP:n förut?"""
    if not ip:
        return True
    traff = (
        await db.execute(
            select(LoginAttempt.id)
            .where(
                LoginAttempt.username == username,
                LoginAttempt.ip == ip,
                LoginAttempt.success.is_(True),
            )
            .limit(1)
        )
    ).first()
    return bool(traff)


async def avisera_inloggning(
    db: AsyncSession, *, user: User, ip: str, user_agent: str, ny_plats: bool
) -> None:
    """Meddelar administratörerna om en inloggning."""
    conf = await installningar(db)
    if not conf.get("avisera_lyckade"):
        return
    from .notify import DEFAULT_SMTP, SMTP_KEY, get_setting, send_email, send_push

    tid = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    rubrik = (
        f"Ny inloggning från okänd adress: {user.username}"
        if ny_plats
        else f"Inloggning: {user.username}"
    )
    text = (
        f"{user.full_name or user.username} loggade in i Borrjournal.\n\n"
        f"Tid: {tid}\nAnvändare: {user.username} ({user.role})\n"
        f"IP-adress: {ip or 'okänd'}\n"
        f"Webbläsare: {user_agent[:120] or 'okänd'}\n\n"
        + (
            "Adressen har inte använts av det här kontot förut.\n\n"
            if ny_plats
            else ""
        )
        + "Var det inte du eller någon i firman, byt lösenord och slå på tvåfaktor.\n"
    )

    admins = (
        await db.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True))
        )
    ).scalars().all()

    # Bara nya platser är värda en push. Allt annat blir brus.
    if ny_plats:
        try:
            await send_push(
                db,
                {"title": rubrik, "body": f"{user.username} från {ip}", "url": "/#/admin/logg"},
                [a.id for a in admins],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[borrjournal] push om inloggning misslyckades: {exc}")

    smtp = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    if not smtp.get("enabled"):
        return
    mottagare = [a.email for a in admins if a.email] or [
        x for x in (smtp.get("recipients") or []) if x
    ]
    if not mottagare:
        return
    try:
        await send_email(smtp, rubrik, text, mottagare)
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] mejl om inloggning misslyckades: {exc}")


async def avisera_sparr(db: AsyncSession, *, username: str, ip: str, antal: int) -> None:
    """Meddelar när ett konto eller en adress spärrats."""
    conf = await installningar(db)
    from . import events

    await events.logga(
        db,
        level="varning",
        source="inloggning",
        message=f"Spärrad efter {antal} misslyckade försök: {username or 'okänt konto'} från {ip}",
        detail=(
            "Kontot och adressen är spärrade en stund. Är det inte någon i firman som "
            "glömt lösenordet handlar det om någon som gissar."
        ),
    )
    if not conf.get("avisera_sparr"):
        return
    from .notify import DEFAULT_SMTP, SMTP_KEY, get_setting, send_email

    smtp = await get_setting(db, SMTP_KEY, DEFAULT_SMTP)
    if not smtp.get("enabled"):
        return
    admins = (
        await db.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True))
        )
    ).scalars().all()
    mottagare = [a.email for a in admins if a.email] or [
        x for x in (smtp.get("recipients") or []) if x
    ]
    if not mottagare:
        return
    try:
        await send_email(
            smtp,
            f"Borrjournal: spärrad inloggning för {username or 'okänt konto'}",
            f"{antal} misslyckade inloggningsförsök från {ip} mot kontot "
            f"{username or '(okänt)'}.\n\nKontot och adressen är spärrade i "
            f"{conf['sparr_minuter']} minuter.\n",
            mottagare,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[borrjournal] mejl om spärr misslyckades: {exc}")


async def stada(db: AsyncSession) -> int:
    conf = await installningar(db)
    grans = datetime.now(timezone.utc) - timedelta(days=int(conf.get("spara_dagar", 90)))
    resultat = await db.execute(delete(LoginAttempt).where(LoginAttempt.at < grans))
    await db.commit()
    return resultat.rowcount or 0
