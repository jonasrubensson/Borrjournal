import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="tekniker")  # admin | tekniker | lasare
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    customer_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    customer_type: Mapped[str] = mapped_column(String(30), default="Privat")
    org_no: Mapped[str] = mapped_column(String(30), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    invoice_address: Mapped[str] = mapped_column(String(255), default="")
    property_designation: Mapped[str] = mapped_column(String(120), default="", index=True)
    address: Mapped[str] = mapped_column(String(200), default="")
    municipality: Mapped[str] = mapped_column(String(80), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    facilities: Mapped[list["Facility"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", lazy="selectin"
    )


class Facility(Base):
    """Brunn, energihål eller pumpanläggning."""

    __tablename__ = "facilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)

    facility_type: Mapped[str] = mapped_column(String(60), default="Bergborrad brunn")
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | soon | action
    drilled_at: Mapped[str] = mapped_column(String(10), default="")

    # Plats. Fritexten behålls som montören skrev den, lat/lon är det systemet räknar med.
    coordinates: Mapped[str] = mapped_column(String(80), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    access_notes: Mapped[str] = mapped_column(Text, default="")
    permit_status: Mapped[str] = mapped_column(String(40), default="")

    # Borrning
    soil_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    casing_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    water_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_lph: Mapped[float | None] = mapped_column(Float, nullable=True)
    bedrock_notes: Mapped[str] = mapped_column(Text, default="")
    water_sample: Mapped[str] = mapped_column(String(40), default="")

    # Pump - egna kolumner så att flottan kan filtreras vid t.ex. fabriksfel
    pump_manufacturer: Mapped[str] = mapped_column(String(80), default="", index=True)
    pump_model: Mapped[str] = mapped_column(String(80), default="", index=True)
    pump_serial: Mapped[str] = mapped_column(String(80), default="", index=True)
    pump_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pump_status: Mapped[str] = mapped_column(String(40), default="")
    pressure_tank: Mapped[str] = mapped_column(String(120), default="")
    pump_installed_at: Mapped[str] = mapped_column(String(10), default="")

    # Service och giltighetstider som påminnelser genereras från
    service_interval_months: Mapped[int] = mapped_column(Integer, default=12)
    last_service_at: Mapped[str] = mapped_column(String(10), default="")
    water_sample_at: Mapped[str] = mapped_column(String(10), default="")
    water_sample_valid_months: Mapped[int] = mapped_column(Integer, default=36)
    certificate_label: Mapped[str] = mapped_column(String(120), default="")
    certificate_expires_at: Mapped[str] = mapped_column(String(10), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    customer: Mapped[Customer] = relationship(back_populates="facilities", lazy="joined")


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    facility_id: Mapped[str | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entry_type: Mapped[str] = mapped_column(String(40), default="Service", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")

    # Sätts av servern, aldrig av klienten
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_name: Mapped[str] = mapped_column(String(120), default="")

    # Rättelser skapar en ny rad som pekar på originalet
    corrects_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # En dragen anteckning raderas inte, den märks. Historiken ska gå att visa upp.
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retracted_by: Mapped[str] = mapped_column(String(120), default="")
    retraction_reason: Mapped[str] = mapped_column(String(255), default="")


class StoredFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    facility_id: Mapped[str | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="SET NULL"), nullable=True
    )
    journal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    thumb_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(20), default="dokument")  # dokument | bild
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    caption: Mapped[str] = mapped_column(String(255), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    uploaded_by: Mapped[str] = mapped_column(String(120), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    facility_id: Mapped[str | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    journal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # service | vattenprov | intyg | uppfoljning | egen
    kind: Mapped[str] = mapped_column(String(20), default="egen", index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[str] = mapped_column(String(10), index=True)
    notify_days_before: Mapped[int] = mapped_column(Integer, default=14)

    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open | done
    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Hindrar att automatiska påminnelser dubbleras vid varje genomsökning
    auto_key: Mapped[str | None] = mapped_column(String(140), nullable=True, unique=True)

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_channels: Mapped[str] = mapped_column(String(60), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[str] = mapped_column(String(120), default="")


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    """Nyckel/värde för inställningar som ska kunna ändras utan omstart."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BackupRecord(Base):
    __tablename__ = "backups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    engine: Mapped[str] = mapped_column(String(20), default="")  # pg_dump | json
    trigger: Mapped[str] = mapped_column(String(20), default="manuell")  # manuell | schemalagd
    status: Mapped[str] = mapped_column(String(20), default="klar")  # klar | fel
    detail: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
