from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field

from .models import Customer, Facility, JournalEntry, StoredFile, User


# ---------- in ----------
class LoginIn(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class UserIn(BaseModel):
    username: str
    password: str
    full_name: str = ""
    email: str | None = None
    role: str = "tekniker"


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    customer_type: str = "Privat"
    org_no: str = ""
    phone: str = ""
    email: str = ""
    invoice_address: str = ""
    property_designation: str = ""
    address: str = ""
    municipality: str = ""
    notes: str = ""


class FacilityIn(BaseModel):
    facility_type: str = "Bergborrad brunn"
    status: str = "ok"
    drilled_at: str = ""
    coordinates: str = ""
    latitude: float | None = None
    longitude: float | None = None
    access_notes: str = ""
    permit_status: str = ""
    soil_depth_m: float | None = None
    casing_length_m: float | None = None
    total_depth_m: float | None = None
    water_level_m: float | None = None
    capacity_lph: float | None = None
    bedrock_notes: str = ""
    water_sample: str = ""
    water_sample_at: str = ""
    water_sample_valid_months: int = 36
    certificate_label: str = ""
    certificate_expires_at: str = ""
    pump_manufacturer: str = ""
    pump_model: str = ""
    pump_serial: str = ""
    pump_depth_m: float | None = None
    pump_status: str = ""
    pressure_tank: str = ""
    pump_installed_at: str = ""
    service_interval_months: int = 12
    last_service_at: str = ""


class NewFacilityIn(BaseModel):
    """Registrering av ny anläggning: skapar kund + anläggning + första journalanteckningen."""

    customer: CustomerIn
    facility: FacilityIn
    existing_customer_id: str | None = None
    first_note: str = ""


class JournalIn(BaseModel):
    facility_id: str | None = None
    entry_type: str = "Service"
    title: str = ""
    body: str = ""
    corrects_id: str | None = None
    # Sätts när montören vill ha en uppföljning bokad på samma gång
    followup_date: str = ""
    followup_title: str = ""


# ---------- ut ----------
def service_due(f: Facility) -> str | None:
    if not f.last_service_at or not f.service_interval_months:
        return None
    try:
        last = date.fromisoformat(f.last_service_at)
    except ValueError:
        return None
    return (last + timedelta(days=30 * f.service_interval_months)).isoformat()


def derived_status(f: Facility) -> str:
    """Manuell status vinner om den är satt till action, annars räknas serviceläget ut."""
    if f.status == "action":
        return "action"
    due = service_due(f)
    if due is None:
        return f.status or "ok"
    today = date.today().isoformat()
    if due < today:
        return "action"
    if due <= (date.today() + timedelta(days=60)).isoformat():
        return "soon"
    return "ok"


def user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "totp_enabled": u.totp_enabled,
        "totp_required": u.totp_required,
        "notify_scope": u.notify_scope,
        "last_login": iso_utc(u.last_login) if u.last_login else None,
    }


def facility_out(f: Facility, with_customer: bool = False) -> dict:
    d = {
        "id": f.id,
        "facility_no": f.facility_no,
        "customer_id": f.customer_id,
        "facility_type": f.facility_type,
        "status": derived_status(f),
        "status_manual": f.status,
        "drilled_at": f.drilled_at,
        "coordinates": f.coordinates,
        "latitude": f.latitude,
        "longitude": f.longitude,
        "geocode_status": f.geocode_status,
        "geocode_message": f.geocode_message,
        "access_notes": f.access_notes,
        "permit_status": f.permit_status,
        "soil_depth_m": f.soil_depth_m,
        "casing_length_m": f.casing_length_m,
        "total_depth_m": f.total_depth_m,
        "water_level_m": f.water_level_m,
        "capacity_lph": f.capacity_lph,
        "bedrock_notes": f.bedrock_notes,
        "water_sample": f.water_sample,
        "water_sample_at": f.water_sample_at,
        "water_sample_valid_months": f.water_sample_valid_months,
        "certificate_label": f.certificate_label,
        "certificate_expires_at": f.certificate_expires_at,
        "pump_manufacturer": f.pump_manufacturer,
        "pump_model": f.pump_model,
        "pump_serial": f.pump_serial,
        "pump_depth_m": f.pump_depth_m,
        "pump_status": f.pump_status,
        "pressure_tank": f.pressure_tank,
        "pump_installed_at": f.pump_installed_at,
        "service_interval_months": f.service_interval_months,
        "last_service_at": f.last_service_at,
        "service_due": service_due(f),
    }
    if with_customer and f.customer is not None:
        d["customer"] = {
            "id": f.customer.id,
            "customer_no": f.customer.customer_no,
            "name": f.customer.name,
            "phone": f.customer.phone,
            "email": f.customer.email,
            "property_designation": f.customer.property_designation,
            "municipality": f.customer.municipality,
        }
    return d


def customer_out(c: Customer, facilities: bool = True) -> dict:
    d = {
        "id": c.id,
        "customer_no": c.customer_no,
        "name": c.name,
        "customer_type": c.customer_type,
        "org_no": c.org_no,
        "phone": c.phone,
        "email": c.email,
        "invoice_address": c.invoice_address,
        "property_designation": c.property_designation,
        "address": c.address,
        "municipality": c.municipality,
        "notes": c.notes,
        "created_at": iso_utc(c.created_at) if c.created_at else None,
        "anonymized_at": iso_utc(c.anonymized_at) if c.anonymized_at else None,
    }
    if facilities:
        d["facilities"] = [facility_out(f) for f in c.facilities]
        statuses = [derived_status(f) for f in c.facilities]
        d["status"] = "action" if "action" in statuses else "soon" if "soon" in statuses else "ok"
    return d


def journal_out(j: JournalEntry, files: list[StoredFile] | None = None) -> dict:
    return {
        "id": j.id,
        "customer_id": j.customer_id,
        "facility_id": j.facility_id,
        "entry_type": j.entry_type,
        "title": j.title,
        "body": j.body,
        "created_at": iso_utc(j.created_at) if j.created_at else None,
        "author_name": j.author_name,
        "corrects_id": j.corrects_id,
        "retracted": j.retracted_at is not None,
        "retracted_at": iso_utc(j.retracted_at) if j.retracted_at else None,
        "retracted_by": j.retracted_by,
        "retraction_reason": j.retraction_reason,
        "attachments": [file_out(f) for f in (files or [])],
    }


def file_out(f: StoredFile) -> dict:
    return {
        "id": f.id,
        "customer_id": f.customer_id,
        "facility_id": f.facility_id,
        "journal_id": f.journal_id,
        "filename": f.filename,
        "kind": f.kind,
        "content_type": f.content_type,
        "size_bytes": f.size_bytes,
        "caption": f.caption,
        "has_thumb": bool(f.thumb_name),
        "uploaded_at": iso_utc(f.uploaded_at) if f.uploaded_at else None,
        "uploaded_by": f.uploaded_by,
    }


def iso_utc(value) -> str | None:
    """Serialiserar tid med tidszon.

    SQLite lagrar tidsstämplar utan zon, så det som kommer tillbaka är naivt.
    Skickas det vidare utan offset tolkar webbläsaren det som lokal tid, och
    en journalrad skriven 22:01 svensk tid visas som 20:01. Allt lagras i UTC,
    så naiva värden märks som UTC här.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return value.isoformat()  # date, saknar tidszon och behöver ingen
