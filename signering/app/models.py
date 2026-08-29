"""Datamodell för signeringstjänsten.

Tjänsten känner inte till kunder, anläggningar eller journaler. Den vet bara
att ett dokument ska signeras av en viss e-postadress, och vad som hänt med
det. Blir tjänsten komprometterad förloras de dokument som ligger där just nu,
ingenting annat.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Signering(Base):
    """Ett dokument som väntar på signatur."""

    __tablename__ = "signeringar"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    # Tokenet skickas till kunden men lagras aldrig, bara hashen. Kommer någon
    # åt databasen går det ändå inte att öppna någon annans länk.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Referens tillbaka till Borrjournal. Bara numret, inga kunduppgifter.
    referens: Mapped[str] = mapped_column(String(40), index=True)
    rubrik: Mapped[str] = mapped_column(String(200), default="")
    avsandare: Mapped[str] = mapped_column(String(200), default="")
    # Personen som skickade, vid sidan av firmanamnet. Ett namn är lättare att
    # känna igen än ett företagsnamn, och alla firmor har inte fyllt i sitt.
    avsandare_person: Mapped[str] = mapped_column(String(200), default="")
    avsandare_epost: Mapped[str] = mapped_column(String(200), default="")
    belopp: Mapped[float] = mapped_column(default=0.0)
    belopp_text: Mapped[str] = mapped_column(String(60), default="")

    mottagare_epost: Mapped[str] = mapped_column(String(200), index=True)
    mottagare_namn: Mapped[str] = mapped_column(String(200), default="")

    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    pdf_hash: Mapped[str] = mapped_column(String(64))
    signerad_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Texter som Borrjournal bestämmer, så att firman styr sin egen ordalydelse
    text_sida: Mapped[str] = mapped_column(Text, default="")
    text_godkann: Mapped[str] = mapped_column(Text, default="")
    text_bevis: Mapped[str] = mapped_column(Text, default="")

    # vantar | oppnad | verifierad | signerad | avbojd | utgangen
    status: Mapped[str] = mapped_column(String(16), default="vantar", index=True)
    namnteckning: Mapped[str] = mapped_column(Text, default="")
    avbojd_orsak: Mapped[str] = mapped_column(String(255), default="")

    skapad: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    giltig_till: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signerad_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Sant när Borrjournal hämtat hem resultatet
    hamtad: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Handelse(Base):
    """Revisionslogg med hashkedja.

    Varje post innehåller hashen av den föregående. Ändrar någon en rad i
    efterhand stämmer inte kedjan längre, och det går att visa. Inte ens den
    som driver tjänsten kan skriva om historien obemärkt.
    """

    __tablename__ = "handelser"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    signering_id: Mapped[str] = mapped_column(String(36), index=True)
    lopnummer: Mapped[int] = mapped_column(Integer, default=1)

    typ: Mapped[str] = mapped_column(String(40))
    beskrivning: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    webblasare: Mapped[str] = mapped_column(String(255), default="")
    epost: Mapped[str] = mapped_column(String(200), default="")

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    foregaende_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64))


class Engangskod(Base):
    """Kod som skickas till mottagarens e-post."""

    __tablename__ = "engangskoder"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    signering_id: Mapped[str] = mapped_column(String(36), index=True)
    kod_hash: Mapped[str] = mapped_column(String(64))
    skapad: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    giltig_till: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    forsok: Mapped[int] = mapped_column(Integer, default=0)
    anvand: Mapped[bool] = mapped_column(Boolean, default=False)



class Installning(Base):
    """Inställningar som Borrjournal skickar hit, till exempel e-postuppgifter.

    Tjänsten har medvetet ingen egen administration. Allt styrs från
    Borrjournal, så att det bara finns ett ställe att ställa in saker på.
    """

    __tablename__ = "installningar"

    nyckel: Mapped[str] = mapped_column(String(40), primary_key=True)
    varde: Mapped[str] = mapped_column(Text, default="")
    uppdaterad: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
