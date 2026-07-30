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


async def _run_backup(conf: dict) -> None:
    async with SessionLocal() as db:
        record = await backup_service.create_backup(db, trigger="schemalagd", actor="system")
        if record.status == "klar":
            removed = await backup_service.prune(db, int(conf.get("keep_days", 30)))
            print(f"[schemaläggare] backup {record.filename} klar, {removed} gamla rensade")
        else:
            print(f"[schemaläggare] backup misslyckades: {record.detail}")


async def _run_reminders() -> None:
    async with SessionLocal() as db:
        created = await reminder_service.generate_auto(db)
        result = await reminder_service.notify_due(db)
        print(
            f"[schemaläggare] påminnelser: {created} nya, {result['reminders']} meddelade "
            f"(e-post: {result['email']}, push: {result['push']})"
        )


async def loop() -> None:
    global _last_backup_day, _last_scan_day
    # Låt appen komma igång innan första kontrollen
    await asyncio.sleep(20)
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

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - schemaläggaren får aldrig dö
            print(f"[schemaläggare] fel: {exc}")

        await asyncio.sleep(60)
