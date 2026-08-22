"""Utskick av påminnelser via e-post och webbpush.

Inställningar ligger i tabellen app_settings så att de kan ändras i gränssnittet utan omstart.
SMTP-lösenordet returneras aldrig av API:et, bara ett tomt eller maskerat värde.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AppSetting, PushSubscription

SMTP_KEY = "smtp"
PUSH_KEY = "vapid"
SCHEDULE_KEY = "backup_schedule"

DEFAULT_SMTP = {
    "enabled": False,
    "host": "",
    "port": 587,
    "security": "starttls",  # starttls | ssl | none
    "username": "",
    "password": "",
    "sender": "",
    "recipients": [],
}

DEFAULT_SCHEDULE = {
    "enabled": True,
    "hour": 2,
    "minute": 30,
    "keep_days": 30,
    "reminder_scan_hour": 6,
}


async def get_setting(db: AsyncSession, key: str, default: dict) -> dict:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None or not row.value:
        return dict(default)
    try:
        return {**default, **json.loads(row.value)}
    except json.JSONDecodeError:
        return dict(default)


async def save_setting(db: AsyncSession, key: str, value: dict) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key=key, value=json.dumps(value, ensure_ascii=False)))
    else:
        row.value = json.dumps(value, ensure_ascii=False)
    await db.commit()


def public_smtp(conf: dict) -> dict:
    safe = {k: v for k, v in conf.items() if k != "password"}
    safe["password_set"] = bool(conf.get("password"))
    return safe


# ---------------- e-post ----------------
async def send_email(
    conf: dict,
    subject: str,
    body: str,
    recipients: list[str] | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    import aiosmtplib

    to = recipients or conf.get("recipients") or []
    if not conf.get("enabled") or not conf.get("host") or not to:
        raise RuntimeError("E-post är inte konfigurerad. Fyll i server och mottagare.")

    message = EmailMessage()
    message["From"] = conf.get("sender") or conf.get("username") or "borrjournal@localhost"
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)

    for filnamn, data, typ in attachments or []:
        huvudtyp, _, undertyp = typ.partition("/")
        message.add_attachment(
            data, maintype=huvudtyp or "application", subtype=undertyp or "octet-stream",
            filename=filnamn,
        )

    security = conf.get("security", "starttls")
    kwargs = {
        "hostname": conf["host"],
        "port": int(conf.get("port") or 587),
        "timeout": 20,
        "start_tls": security == "starttls",
        "use_tls": security == "ssl",
    }
    if conf.get("username"):
        kwargs["username"] = conf["username"]
        kwargs["password"] = conf.get("password") or ""

    try:
        await aiosmtplib.send(message, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(tolka_smtp_fel(exc, conf)) from exc


def tolka_smtp_fel(exc: Exception, conf: dict) -> str:
    """Översätter SMTP-fel till något som går att göra något åt.

    Servrarnas egna meddelanden säger sällan vad man ska ändra. Gmail och
    Microsoft har dessutom var sin fälla som inte har med lösenordet att göra.
    """
    text = str(exc)
    host = (conf.get("host") or "").lower()
    kod = ""
    for m in ("535", "550", "554", "530", "421"):
        if m in text:
            kod = m
            break

    if "5.7.139" in text or "5.7.30" in text or (kod == "535" and "office365" in host):
        return (
            "Microsoft nekar inloggningen. Det beror nästan aldrig på lösenordet: "
            "SMTP AUTH är avstängt som standard för varje brevlåda. En administratör "
            "behöver slå på Autentiserad SMTP för kontot i Microsoft 365 admin center, "
            "under Användare, Aktiva användare, E-post, Hantera e-postappar. "
            f"({text[:120]})"
        )
    if kod == "535" and "gmail" in host:
        return (
            "Google nekar inloggningen. Med tvåstegsverifiering går det inte att använda "
            "ditt vanliga lösenord: skapa ett app-lösenord på "
            "myaccount.google.com/apppasswords och använd det i stället. "
            f"({text[:120]})"
        )
    if kod == "535":
        return f"Servern nekade användarnamn eller lösenord. ({text[:150]})"
    if "5.7.60" in text or "SendAsDenied" in text:
        return (
            "Avsändaradressen får inte skicka från det inloggade kontot. Sätt avsändaren "
            "till samma adress som användarnamnet, eller ge kontot rätt att skicka som "
            f"den adressen. ({text[:120]})"
        )
    if "certificate" in text.lower() or "ssl" in text.lower():
        return (
            "TLS-anslutningen gick inte igenom. Kontrollera att port och krypteringsval "
            "hör ihop: 587 med STARTTLS, eller 465 med SSL. Microsoft 365 stöder bara "
            f"587 med STARTTLS. ({text[:120]})"
        )
    if "timed out" in text.lower() or "timeout" in text.lower():
        return (
            "Servern svarade inte i tid. Kontrollera adress och port, och att "
            f"brandväggen släpper ut trafik dit. ({text[:120]})"
        )
    if "Connection refused" in text or "getaddrinfo" in text:
        return f"Nådde inte servern. Kontrollera adressen och att servern är nåbar. ({text[:120]})"
    return f"E-posten gick inte iväg: {text[:200]}"


# Färdiga inställningar för de vanligaste leverantörerna.
LEVERANTORER = {
    "gmail": {
        "namn": "Gmail eller Google Workspace",
        "host": "smtp.gmail.com",
        "port": 587,
        "security": "starttls",
        "hjalp": (
            "Användarnamn är hela adressen. Har kontot tvåstegsverifiering måste du skapa "
            "ett app-lösenord på myaccount.google.com/apppasswords och använda det, inte "
            "ditt vanliga lösenord."
        ),
    },
    "smtp2go": {
        "namn": "SMTP2GO",
        "host": "mail.smtp2go.com",
        "port": 587,
        "security": "starttls",
        "hjalp": (
            "Skapa en SMTP-användare i deras panel, det är den du loggar in med, inte "
            "ditt kontolösenord. Verifiera er avsändardomän så att mejlen inte hamnar i "
            "skräpposten. Portarna 587, 2525 och 8025 fungerar om brandväggen stoppar 587."
        ),
    },
    "brevo": {
        "namn": "Brevo",
        "host": "smtp-relay.brevo.com",
        "port": 587,
        "security": "starttls",
        "hjalp": (
            "Användarnamnet är den inloggning du får under SMTP och API, och lösenordet är "
            "den SMTP-nyckel som skapas där. Verifiera avsändardomänen."
        ),
    },
    "mailgun": {
        "namn": "Mailgun",
        "host": "smtp.eu.mailgun.org",
        "port": 587,
        "security": "starttls",
        "hjalp": (
            "Använd den europeiska servern om kontot är skapat i EU, annars "
            "smtp.mailgun.org. Användarnamn och lösenord hittas under Sending, Domain "
            "settings, SMTP credentials."
        ),
    },
    "postmark": {
        "namn": "Postmark",
        "host": "smtp.postmarkapp.com",
        "port": 587,
        "security": "starttls",
        "hjalp": (
            "Både användarnamn och lösenord är samma Server API Token. Avsändaradressen "
            "måste vara en verifierad Sender Signature."
        ),
    },
    "office365": {
        "namn": "Microsoft 365 eller Outlook",
        "host": "smtp.office365.com",
        "port": 587,
        "security": "starttls",
        "hjalp": (
            "Bara port 587 med STARTTLS fungerar, inte 465. En administratör måste dessutom "
            "slå på Autentiserad SMTP för brevlådan, det är avstängt som standard. "
            "Microsoft håller på att fasa ut lösenordsinloggning för SMTP, så räkna med att "
            "det behöver bytas mot en tjänst för utskick längre fram."
        ),
    },
    "eget": {
        "namn": "Annan eller egen server",
        "host": "",
        "port": 587,
        "security": "starttls",
        "hjalp": "Fyll i uppgifterna du fått av den som driver e-postservern.",
    },
}


# ---------------- webbpush ----------------
async def ensure_vapid(db: AsyncSession) -> dict:
    """Skapar nyckelpar första gången. Den privata nyckeln lämnar aldrig servern."""
    conf = await get_setting(db, PUSH_KEY, {"public": "", "private": "", "subject": ""})
    if conf.get("public") and conf.get("private"):
        return conf

    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731
    conf = {
        "public": b64(public_raw),
        "private": b64(private_der),
        "subject": conf.get("subject") or "mailto:admin@localhost",
    }
    await save_setting(db, PUSH_KEY, conf)
    return conf


def _send_push_sync(sub: dict, payload: dict, vapid: dict) -> None:
    import base64

    from py_vapid import Vapid01
    from pywebpush import webpush

    der = base64.urlsafe_b64decode(vapid["private"] + "=" * (-len(vapid["private"]) % 4))
    vapid_obj = Vapid01.from_raw_private(der) if hasattr(Vapid01, "from_raw_private") else None
    if vapid_obj is None:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_der_private_key(der, password=None)
        vapid_obj = Vapid01()
        vapid_obj.private_key = key

    webpush(
        subscription_info={
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        },
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=vapid_obj,
        vapid_claims={"sub": vapid.get("subject") or "mailto:admin@localhost"},
        timeout=15,
    )


async def send_push(db: AsyncSession, payload: dict, user_ids: list[str] | None = None) -> int:
    vapid = await ensure_vapid(db)
    stmt = select(PushSubscription)
    if user_ids:
        stmt = stmt.where(PushSubscription.user_id.in_(user_ids))
    subs = (await db.execute(stmt)).scalars().all()

    sent = 0
    for sub in subs:
        data = {"endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth}
        try:
            await asyncio.to_thread(_send_push_sync, data, payload, vapid)
            sub.last_used_at = datetime.now(timezone.utc)
            sub.failures = 0
            sent += 1
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            sub.failures += 1
            # 404/410 betyder att prenumerationen är död, då städar vi bort den
            if "410" in text or "404" in text or sub.failures >= 5:
                await db.delete(sub)
    await db.commit()
    return sent
