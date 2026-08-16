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
    # Krav satt på just den här användaren. Ett globalt krav kan också gälla.
    totp_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # Vilka påminnelser användaren vill bli meddelad om: mina | alla | inga
    notify_scope: Mapped[str] = mapped_column(String(10), default="mina")
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
    # Anonymiserad enligt GDPR: personuppgifterna borttagna, teknik och
    # bokföringsunderlag kvar
    anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
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
    geocode_status: Mapped[str] = mapped_column(String(20), default="")
    geocode_message: Mapped[str] = mapped_column(String(255), default="")

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
    # Klockslag på förfallodagen, tomt = hela dagen
    due_time: Mapped[str] = mapped_column(String(5), default="")
    # Exakt tidpunkt då påminnelsen ska gå ut, i UTC. Sätts av klienten som
    # räknar om från lokal tid, så 08:00 betyder 08:00 hos användaren.
    remind_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
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


class SguWell(Base):
    """Cache av SGU:s brunnsarkiv. Ersätts vid varje synk, ingen egen data här."""

    __tablename__ = "sgu_wells"

    brunnsid: Mapped[str] = mapped_column(String(30), primary_key=True)
    lanskod: Mapped[str] = mapped_column(String(4), index=True)
    kommunkod: Mapped[str] = mapped_column(String(6), default="")
    n: Mapped[float] = mapped_column(Float, index=True)
    e: Mapped[float] = mapped_column(Float, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    lagesnoggrannhet: Mapped[str] = mapped_column(String(4), default="")
    fastighet: Mapped[str] = mapped_column(String(120), default="")
    ort: Mapped[str] = mapped_column(String(80), default="")
    borrdatum: Mapped[str] = mapped_column(String(10), default="")
    totaldjup: Mapped[float | None] = mapped_column(Float, nullable=True)
    djup_till_berg: Mapped[float | None] = mapped_column(Float, nullable=True)
    vattenmangd: Mapped[float | None] = mapped_column(Float, nullable=True)
    grundvattenniva: Mapped[float | None] = mapped_column(Float, nullable=True)
    foderror_till: Mapped[float | None] = mapped_column(Float, nullable=True)
    anvandning: Mapped[str] = mapped_column(String(10), default="", index=True)
    tatning: Mapped[str] = mapped_column(String(10), default="")
    # Tecken före jorddjup respektive vattenmängd. ">" betyder att värdet är en
    # undre gräns: berget ligger djupare än så, kapaciteten är minst så mycket.
    # Utan detta läses "berg djupare än 15 m" som "berg på exakt 15 m".
    tecken_jord: Mapped[str] = mapped_column(String(2), default="")
    tecken_vatten: Mapped[str] = mapped_column(String(2), default="")
    anmarkning: Mapped[str] = mapped_column(String(255), default="")
    hamtad_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Visit(Base):
    """Ett platsbesök innan det finns en kund.

    Poängen är att slippa lägga upp en kund för någon som kanske aldrig blir det.
    Här sparas bara det som behövs för att åka dit och lämna ett pris. Blir det
    affär skapas kunden av besöket, och besöket följer med som historik.
    """

    __tablename__ = "visits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visit_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    # planerat | genomfort | offert | vunnen | forlorad
    status: Mapped[str] = mapped_column(String(20), default="planerat", index=True)
    planned_at: Mapped[str] = mapped_column(String(10), default="")

    contact_name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")

    property_designation: Mapped[str] = mapped_column(String(120), default="", index=True)
    address: Mapped[str] = mapped_column(String(200), default="")
    municipality: Mapped[str] = mapped_column(String(80), default="")
    coordinates: Mapped[str] = mapped_column(String(80), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Adressuppslaget sker i bakgrunden. Status: "" | pagar | klar | ungefarlig | misslyckades
    geocode_status: Mapped[str] = mapped_column(String(20), default="")
    geocode_message: Mapped[str] = mapped_column(String(255), default="")

    errand: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    quote_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_sent_at: Mapped[str] = mapped_column(String(10), default="")
    lost_reason: Mapped[str] = mapped_column(String(255), default="")

    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ShareLog(Base):
    """Vad som skickats ut till externa borrare, och av vem."""

    __tablename__ = "share_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    visit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255), default="")
    fields: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    sent_by: Mapped[str] = mapped_column(String(120), default="")


class Article(Base):
    """Artikel i lagret eller på prislistan."""

    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    article_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60), default="", index=True)
    unit: Mapped[str] = mapped_column(String(20), default="st")

    purchase_price: Mapped[float] = mapped_column(Float, default=0.0)
    sales_price: Mapped[float] = mapped_column(Float, default=0.0)
    vat_percent: Mapped[float] = mapped_column(Float, default=25.0)

    # Lager. track_stock av för tjänster och sådant som inte lagerhålls.
    track_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    stock: Mapped[float] = mapped_column(Float, default=0.0)
    min_stock: Mapped[float] = mapped_column(Float, default=0.0)
    supplier: Mapped[str] = mapped_column(String(120), default="")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class StockMovement(Base):
    """Varje lagerförändring, så att ett saldo alltid går att förklara."""

    __tablename__ = "stock_movements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    article_id: Mapped[str] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    change: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(40), default="")  # inkop | forbrukning | justering
    work_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    by_user: Mapped[str] = mapped_column(String(120), default="")


class Quote(Base):
    """Offert. Kan höra till ett platsbesök innan kunden finns."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    quote_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    facility_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    visit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # utkast | skickad | accepterad | avslagen | utgangen
    status: Mapped[str] = mapped_column(String(20), default="utkast", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    terms: Mapped[str] = mapped_column(Text, default="")

    # Sparas på offerten, inte hämtas från kunden, så en gammal offert alltid
    # visar det som faktiskt stod i den när den skickades
    recipient_name: Mapped[str] = mapped_column(String(200), default="")
    recipient_address: Mapped[str] = mapped_column(String(255), default="")
    recipient_email: Mapped[str] = mapped_column(String(200), default="")

    valid_until: Mapped[str] = mapped_column(String(10), default="")
    rot_deduction: Mapped[bool] = mapped_column(Boolean, default=False)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_to: Mapped[str] = mapped_column(String(255), default="")
    decided_at: Mapped[str] = mapped_column(String(10), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkOrder(Base):
    """Arbetsorder: vad som faktiskt gjordes och gick åt.

    Journalen berättar vad som hände. Arbetsordern håller reda på vad det kostade,
    så att inget glöms bort vid faktureringen.
    """

    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    # Får saknas medan ordern är ett utkast. Ute i fält vill man kunna börja
    # skriva vad som går åt innan man letat upp rätt kund i registret.
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    facility_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    quote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    journal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # oppen | utford | fakturerad | betald | makulerad
    status: Mapped[str] = mapped_column(String(20), default="oppen", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    performed_at: Mapped[str] = mapped_column(String(10), default="")
    performed_by: Mapped[str] = mapped_column(String(120), default="")

    invoiced_at: Mapped[str] = mapped_column(String(10), default="")
    invoice_no: Mapped[str] = mapped_column(String(40), default="")
    paid_at: Mapped[str] = mapped_column(String(10), default="")

    rot_deduction: Mapped[bool] = mapped_column(Boolean, default=False)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0)
    # Lagret dras när ordern markeras utförd, en gång
    stock_deducted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class LineItem(Base):
    """Rad på en offert eller arbetsorder.

    Benämning och pris kopieras från artikeln vid tillägg. Ändras artikelns pris
    senare påverkas inte gamla offerter och order, vilket är hela poängen.
    """

    __tablename__ = "line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    work_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    article_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20), default="material")  # material | arbete | ovrigt
    article_no: Mapped[str] = mapped_column(String(30), default="")
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(String(255), default="")
    unit: Mapped[str] = mapped_column(String(20), default="st")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    vat_percent: Mapped[float] = mapped_column(Float, default=25.0)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0)


class QuoteTemplate(Base):
    """Offertmall. Rubrik, texter och färdiga rader för ett återkommande jobb."""

    __tablename__ = "quote_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    intro: Mapped[Text] = mapped_column(Text, default="")
    terms: Mapped[Text] = mapped_column(Text, default="")
    valid_days: Mapped[int] = mapped_column(Integer, default=30)
    # Rader som JSON, eftersom de är en del av mallen och inte egna poster
    lines: Mapped[str] = mapped_column(Text, default="[]")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_by: Mapped[str] = mapped_column(String(120), default="")



class SystemEvent(Base):
    """Saker som gick fel i bakgrunden, synligt i appen.

    Ett bakgrundsjobb som misslyckas har ingen användare att svara. Utan den här
    tabellen blir felet en rad i containerloggen som ingen läser.
    """

    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    level: Mapped[str] = mapped_column(String(10), default="fel", index=True)  # fel | varning | info
    source: Mapped[str] = mapped_column(String(40), default="", index=True)
    message: Mapped[str] = mapped_column(String(500))
    detail: Mapped[str] = mapped_column(Text, default="")
    object_type: Mapped[str] = mapped_column(String(30), default="")
    object_id: Mapped[str] = mapped_column(String(36), default="")
    reference: Mapped[str] = mapped_column(String(12), default="", index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class LoginAttempt(Base):
    """Varje inloggningsförsök, lyckat som misslyckat.

    Behövs för tre saker: att blockera efter upprepade misslyckanden, att kunna
    svara på frågan vem som loggat in varifrån, och att se mönster i efterhand.
    """

    __tablename__ = "login_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reason: Mapped[str] = mapped_column(String(60), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
