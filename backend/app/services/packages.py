"""Kundpaket: export och återläsning av en eller alla kunder.

Skiljer sig från backupen. En backup är en ögonblicksbild av hela systemet som skrivs
över allt vid återläsning. Ett kundpaket är fristående och kan läsas in i ett system
som redan har data, utan att röra det som finns. Det är vad man vill ha när en
enskild kund ska flyttas, återskapas efter en felaktig radering, eller när ett nytt
system ska fyllas kund för kund.

Paketet innehåller läsbara filnamn och en kund.json som går att öppna i vilken
texteditor som helst.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Customer, Facility, JournalEntry, Reminder, StoredFile

FILE_DIR = os.path.join(settings.data_dir, "files")
THUMB_DIR = os.path.join(settings.data_dir, "thumbs")

PAKET_VERSION = 1


def _trygg(text: str, fallback: str = "namnlos") -> str:
    rent = "".join(c if c.isalnum() or c in " .-_()" else "_" for c in (text or "")).strip()
    return rent[:80] or fallback


def _radera_interna(rad: dict) -> dict:
    """Tar bort id och kopplingar. De sätts om vid inläsning så att paketet kan
    läsas in i ett system där id:na redan är upptagna."""
    return {k: v for k, v in rad.items() if k not in ("id", "customer_id", "facility_id")}


def _serialisera(obj, hoppa: set[str] = frozenset()) -> dict:
    ut = {}
    for kolumn in obj.__table__.columns:
        if kolumn.name in hoppa:
            continue
        varde = getattr(obj, kolumn.name)
        ut[kolumn.name] = varde.isoformat() if isinstance(varde, datetime) else varde
    return ut


async def bygg_paket(db: AsyncSession, customer_ids: list[str]) -> bytes:
    """Bygger ett tar.gz med de valda kunderna och deras filer."""
    buffer = io.BytesIO()
    kunder_ut = []
    filer_att_lagga = []

    for cid in customer_ids:
        kund = (
            await db.execute(select(Customer).where(Customer.id == cid))
        ).unique().scalar_one_or_none()
        if kund is None:
            continue

        anlaggningar = (
            await db.execute(select(Facility).where(Facility.customer_id == cid))
        ).unique().scalars().all()
        journal = (
            await db.execute(
                select(JournalEntry)
                .where(JournalEntry.customer_id == cid)
                .order_by(JournalEntry.created_at)
            )
        ).scalars().all()
        filer = (
            await db.execute(select(StoredFile).where(StoredFile.customer_id == cid))
        ).scalars().all()
        paminnelser = (
            await db.execute(select(Reminder).where(Reminder.customer_id == cid))
        ).scalars().all()

        anl_nyckel = {f.id: f.facility_no for f in anlaggningar}
        mapp = f"kunder/{_trygg(kund.customer_no)}-{_trygg(kund.name)}"

        fil_ut = []
        for f in filer:
            kalla = os.path.join(FILE_DIR, f.stored_name)
            if not os.path.exists(kalla):
                continue
            underkatalog = "bilder" if f.kind == "bild" else "dokument"
            arkivnamn = f"{mapp}/{underkatalog}/{f.stored_name[:8]}-{_trygg(f.filename, 'fil')}"
            filer_att_lagga.append((kalla, arkivnamn))
            rad = _serialisera(f, {"id", "customer_id", "facility_id", "journal_id"})
            rad["facility_no"] = anl_nyckel.get(f.facility_id)
            rad["arkivnamn"] = arkivnamn
            fil_ut.append(rad)

        kunder_ut.append(
            {
                "kund": _serialisera(kund, {"id"}),
                "anlaggningar": [_serialisera(a, {"id", "customer_id"}) for a in anlaggningar],
                "journal": [
                    {
                        **_serialisera(j, {"id", "customer_id", "facility_id"}),
                        "facility_no": anl_nyckel.get(j.facility_id),
                    }
                    for j in journal
                ],
                "paminnelser": [
                    {
                        **_serialisera(r, {"id", "customer_id", "facility_id", "auto_key"}),
                        "facility_no": anl_nyckel.get(r.facility_id),
                    }
                    for r in paminnelser
                ],
                "filer": fil_ut,
                "mapp": mapp,
            }
        )

    manifest = {
        "app": "borrjournal",
        "typ": "kundpaket",
        "version": PAKET_VERSION,
        "skapat": datetime.now(timezone.utc).isoformat(),
        "antal_kunder": len(kunder_ut),
        "antal_filer": len(filer_att_lagga),
        "innehall": (
            "kunder.json innehåller alla uppgifter. Katalogen kunder/ innehåller samma "
            "dokument och bilder med läsbara namn, sorterade per kund. Läses in med "
            "Inställningar → Backup → Läs in kundpaket, eller med "
            "python -m app.import_customers paket.tar.gz"
        ),
    }

    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for data, namn in (
            (json.dumps({"manifest": manifest, "kunder": kunder_ut}, ensure_ascii=False, indent=1), "kunder.json"),
            (json.dumps(manifest, ensure_ascii=False, indent=2), "manifest.json"),
        ):
            raw = data.encode("utf-8")
            info = tarfile.TarInfo(namn)
            info.size = len(raw)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(info, io.BytesIO(raw))
        for kalla, arkivnamn in filer_att_lagga:
            tar.add(kalla, arcname=arkivnamn)

    buffer.seek(0)
    return buffer.read()


def _tid(varde):
    if not varde:
        return None
    try:
        d = datetime.fromisoformat(str(varde).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def las_in_paket(
    db: AsyncSession, data: bytes, *, actor: str = "", ersatt: bool = False
) -> dict:
    """Läser in ett kundpaket.

    Nya id:n sätts genomgående, så paketet kan läsas in i ett system som redan har
    data. Kunder som redan finns med samma kundnummer hoppas över, om inte ersatt
    är satt, då raderas den befintliga först.
    """
    from .backup import _lasbart_namn  # noqa: F401  håller modulerna ihop

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        try:
            fil = tar.extractfile("kunder.json")
        except KeyError:
            raise ValueError("Filen är inget kundpaket, kunder.json saknas") from None
        if fil is None:
            raise ValueError("Kunde inte läsa kunder.json ur paketet")
        innehall = json.load(fil)

        if innehall.get("manifest", {}).get("typ") != "kundpaket":
            raise ValueError("Filen är inget kundpaket")

        os.makedirs(FILE_DIR, exist_ok=True)
        os.makedirs(THUMB_DIR, exist_ok=True)

        skapade, hoppade, ersatta, filer_in = [], [], [], 0

        for post in innehall.get("kunder", []):
            kunddata = post["kund"]
            kundnr = kunddata.get("customer_no", "")

            befintlig = (
                await db.execute(select(Customer).where(Customer.customer_no == kundnr))
            ).unique().scalar_one_or_none()
            if befintlig is not None:
                if not ersatt:
                    hoppade.append(f"{kundnr} {kunddata.get('name', '')}")
                    continue
                await db.delete(befintlig)
                await db.flush()
                ersatta.append(kundnr)

            kund = Customer(
                id=str(uuid.uuid4()),
                **{
                    k: v
                    for k, v in kunddata.items()
                    if k not in ("created_at", "updated_at", "id")
                },
            )
            kund.created_at = _tid(kunddata.get("created_at")) or datetime.now(timezone.utc)
            db.add(kund)
            await db.flush()

            anl_id = {}
            for a in post.get("anlaggningar", []):
                facility = Facility(
                    id=str(uuid.uuid4()),
                    customer_id=kund.id,
                    **{
                        k: v
                        for k, v in a.items()
                        if k not in ("created_at", "updated_at", "id", "customer_id")
                    },
                )
                facility.created_at = _tid(a.get("created_at")) or datetime.now(timezone.utc)
                db.add(facility)
                await db.flush()
                anl_id[a.get("facility_no")] = facility.id

            for j in post.get("journal", []):
                rad = {
                    k: v
                    for k, v in j.items()
                    if k not in ("facility_no", "created_at", "id", "customer_id", "facility_id")
                }
                entry = JournalEntry(
                    id=str(uuid.uuid4()),
                    customer_id=kund.id,
                    facility_id=anl_id.get(j.get("facility_no")),
                    **rad,
                )
                # Originaltiden bevaras, annars tappar journalen sitt värde
                entry.created_at = _tid(j.get("created_at")) or datetime.now(timezone.utc)
                db.add(entry)

            for r in post.get("paminnelser", []):
                rad = {
                    k: v
                    for k, v in r.items()
                    if k
                    not in (
                        "facility_no",
                        "created_at",
                        "remind_at",
                        "notified_at",
                        "completed_at",
                        "id",
                        "customer_id",
                        "facility_id",
                        "journal_id",
                    )
                }
                paminnelse = Reminder(
                    id=str(uuid.uuid4()),
                    customer_id=kund.id,
                    facility_id=anl_id.get(r.get("facility_no")),
                    **rad,
                )
                paminnelse.remind_at = _tid(r.get("remind_at"))
                paminnelse.notified_at = _tid(r.get("notified_at"))
                paminnelse.completed_at = _tid(r.get("completed_at"))
                db.add(paminnelse)

            for f in post.get("filer", []):
                arkivnamn = f.get("arkivnamn")
                if not arkivnamn:
                    continue
                try:
                    kalla = tar.extractfile(arkivnamn)
                except KeyError:
                    kalla = None
                if kalla is None:
                    continue

                nytt_lagrat = f"{uuid.uuid4()}{os.path.splitext(f.get('stored_name', ''))[1]}"
                mal = os.path.join(FILE_DIR, nytt_lagrat)
                with open(mal, "wb") as ut:
                    shutil.copyfileobj(kalla, ut)

                rad = {
                    k: v
                    for k, v in f.items()
                    if k
                    not in (
                        "facility_no",
                        "arkivnamn",
                        "stored_name",
                        "thumb_name",
                        "uploaded_at",
                        "facility_id",
                        "customer_id",
                        "journal_id",
                        "id",
                    )
                }
                post_fil = StoredFile(
                    id=str(uuid.uuid4()),
                    customer_id=kund.id,
                    facility_id=anl_id.get(f.get("facility_no")),
                    stored_name=nytt_lagrat,
                    **rad,
                )
                post_fil.uploaded_at = _tid(f.get("uploaded_at")) or datetime.now(timezone.utc)

                # Tumnaglar skapas om, i stället för att bäras med i paketet
                with open(mal, "rb") as las:
                    raw = las.read()
                from ..routers.files import make_pdf_thumb, make_thumb

                if post_fil.kind == "bild":
                    post_fil.thumb_name = make_thumb(raw, nytt_lagrat)
                elif (post_fil.content_type or "").endswith("pdf"):
                    post_fil.thumb_name = make_pdf_thumb(raw, nytt_lagrat)

                db.add(post_fil)
                filer_in += 1

            skapade.append(f"{kund.customer_no} {kund.name}")

        await db.commit()

    return {
        "skapade": skapade,
        "hoppade": hoppade,
        "ersatta": ersatta,
        "filer": filer_in,
    }
