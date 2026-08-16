"""Enkel schemaläggare i processen.

Kör en gång per minut och gör något bara när klockan passerat inställd tidpunkt och jobbet
inte redan körts idag. Medvetet enkel: inga externa köer, inget Celery.

Obs: kör appen med en (1) worker, annars gör flera processer samma jobb.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime

from .db import SessionLocal
from .services import backup as backup_service
from .services import reminders as reminder_service
from .services.notify import DEFAULT_SCHEDULE, SCHEDULE_KEY, get_setting

_last_backup_day: str | None = None
_last_scan_day: str | None = None
_last_sgu_day: str | None = None


async def _run_backup(conf: dict) -> None:
    async with SessionLocal() as db:
        record = await backup_service.create_backup(db, trigger="schemalagd", actor="system")
        if record.status == "klar":
            removed = await backup_service.prune(db, int(conf.get("keep_days", 30)))
            print(f"[schemaläggare] backup {record.filename} klar, {removed} gamla rensade")
        else:
            print(f"[schemaläggare] backup misslyckades: {record.detail}")


async def _run_reminders() -> None:
    """Genererar de automatiska en gång per dygn."""
    async with SessionLocal() as db:
        created = await reminder_service.generate_auto(db)
        affar = await reminder_service.generate_business(db)
        stangda = await reminder_service.stang_inaktuella(db)
        await reminder_service.backfill_remind_at(db)
        if created or affar or stangda:
            print(
                f"[schemaläggare] påminnelser: {created} service, {affar} affär, "
                f"{stangda} inaktuella stängda"
            )


async def _check_reminders() -> None:
    """Skickar det som förfallit. Körs ofta, så en påminnelse satt till 07:30
    går ut 07:30 och inte vid nästa dygnsgenomgång."""
    async with SessionLocal() as db:
        result = await reminder_service.notify_due(db)
        if result["reminders"]:
            print(
                f"[schemaläggare] {result['reminders']} påminnelser skickade "
                f"(e-post: {result['email']}, push: {result['push']})"
            )


async def _run_sgu() -> None:
    """Håller de valda länen uppdaterade.

    Hämtar län som saknas helt, och sådana som är äldre än inställt antal dagar.
    SGU uppdaterar sina öppna data en gång i veckan, så oftare är onödigt.
    """
    from sqlalchemy import func, select

    from .models import SguWell
    from .services import sgu as sgu_service
    from .services.notify import get_setting

    async with SessionLocal() as db:
        conf = await get_setting(db, "sgu", {"lan": [], "auto": True, "dagar": 7})
        if not conf.get("auto") or not conf.get("lan"):
            return

        befintliga = dict(
            (
                await db.execute(
                    select(SguWell.lanskod, func.max(SguWell.hamtad_at)).group_by(SguWell.lanskod)
                )
            ).all()
        )
        # Län som efterfrågats men saknas läggs till automatiskt, så att ingen
        # behöver veta vilka län firman jobbar i.
        onskade = list(dict.fromkeys(list(conf["lan"]) + list(conf.get("auto_lan") or [])))
        for lanskod in onskade:
            hamtad = befintliga.get(lanskod)
            if not sgu_service.is_stale(hamtad, dagar=int(conf.get("dagar", 7))):
                continue
            try:
                r = await sgu_service.sync_lan(db, lanskod)
                print(
                    f"[schemaläggare] SGU {r['namn']}: {r['sparade']} brunnar "
                    f"på {r['sekunder']} s"
                )
            except Exception as exc:  # noqa: BLE001
                from .services import events

                await events.logga(
                    db,
                    level="varning",
                    source="sgu",
                    message=f"Hämtningen av län {lanskod} misslyckades",
                    detail=str(exc),
                )


async def _stada_handelser() -> None:
    from .services import events

    async with SessionLocal() as db:
        await events.stada(db)


async def loop() -> None:
    global _last_backup_day, _last_scan_day, _last_sgu_day
    # Låt appen komma igång innan första kontrollen
    await asyncio.sleep(20)

    # Hämtar SGU direkt vid start om något valt län saknas eller är gammalt.
    try:
        await _run_sgu()
    except Exception as exc:  # noqa: BLE001
        print(f"[schemaläggare] första SGU-kontrollen misslyckades: {exc}")
    while True:
        try:
            async with SessionLocal() as db:
                conf = await get_setting(db, SCHEDULE_KEY, DEFAULT_SCHEDULE)

            now = datetime.now()
            today = date.today().isoformat()
            after = lambda h, m: (now.hour, now.minute) >= (int(h), int(m))  # noqa: E731

            if conf.get("enabled") and _last_backup_day != today and after(
                conf.get("hour", 2), conf.get("minute", 30)
            ):
                _last_backup_day = today
                await _run_backup(conf)

            if _last_scan_day != today and after(conf.get("reminder_scan_hour", 6), 0):
                _last_scan_day = today
                await _run_reminders()

            # Efter påminnelserna, samma tid på dygnet. Hoppar över län som är färska.
            if _last_sgu_day != today and after(conf.get("reminder_scan_hour", 6), 30):
                _last_sgu_day = today
                await _run_sgu()
                await _stada_handelser()

            # Förfallna påminnelser kollas varje varv, alltså varje minut
            await _check_reminders()

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - schemaläggaren får aldrig dö
            print(f"[schemaläggare] fel: {exc}")

        await asyncio.sleep(60)
