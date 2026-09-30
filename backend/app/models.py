"""Database schema.

IDs for hospitals and doctors are human-readable strings (HOSP-001, DOC-001) on purpose:
the AI may only refer to a provider by an ID that a tool returned, and readable IDs make
that grounding rule easy to audit in logs and tests.
"""
from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    text,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Specialty(Base):
    __tablename__ = "specialties"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. "cardiology"
    name_en: Mapped[str] = mapped_column(String(80))
    name_ar: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200))


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # HOSP-001
    name_en: Mapped[str] = mapped_column(String(120))
    name_ar: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2), index=True)  # ISO 3166-1 alpha-2
    city: Mapped[str] = mapped_column(String(60), index=True)
    address: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(40))  # IANA, slots are generated in local time
    has_emergency: Mapped[bool] = mapped_column(Boolean, index=True)
    accreditation: Mapped[str | None] = mapped_column(String(40))
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(5)))  # ["ar", "en"]
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)

    doctors: Mapped[list["Doctor"]] = relationship(back_populates="hospital")


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # DOC-001
    name_en: Mapped[str] = mapped_column(String(120))
    name_ar: Mapped[str] = mapped_column(String(120))
    specialty_code: Mapped[str] = mapped_column(ForeignKey("specialties.code"), index=True)
    hospital_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id"), index=True)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(5)))
    years_experience: Mapped[int] = mapped_column(Integer)
    offers_second_opinion: Mapped[bool] = mapped_column(Boolean, default=False)
    offers_remote_consult: Mapped[bool] = mapped_column(Boolean, default=False)  # review before travel
    consultation_fee: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)

    hospital: Mapped[Hospital] = relationship(back_populates="doctors")
    specialty: Mapped[Specialty] = relationship()

    __table_args__ = (CheckConstraint("years_experience >= 0", name="ck_doctor_experience"),)


class Slot(Base):
    """An appointment slot. Status transitions are owned by the booking service (M6), never the AI."""

    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    mode: Mapped[str] = mapped_column(String(10))  # in_person | remote
    status: Mapped[str] = mapped_column(String(10), default="open")
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("doctor_id", "starts_at", name="uq_slot_doctor_time"),
        CheckConstraint("status IN ('open','held','booked')", name="ck_slot_status"),
        CheckConstraint("mode IN ('in_person','remote')", name="ck_slot_mode"),
        Index("ix_slot_lookup", "doctor_id", "status", "starts_at"),
    )


class Patient(Base):
    """Mock patient file (the hospital CRM record). Looked up only after identity verification
    (file number or national ID + date of birth). Fictional people only."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_number: Mapped[str] = mapped_column(String(12), unique=True)      # MRN-100001
    national_id: Mapped[str] = mapped_column(String(10), unique=True)
    date_of_birth: Mapped[date] = mapped_column(Date)
    name_en: Mapped[str] = mapped_column(String(80))
    name_ar: Mapped[str] = mapped_column(String(80))
    sex: Mapped[str] = mapped_column(String(1))                            # M | F (for Mr./Ms.)
    city: Mapped[str] = mapped_column(String(60))
    preferred_language: Mapped[str] = mapped_column(String(2))
    known_conditions: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    allergies: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    last_visit_date: Mapped[date | None] = mapped_column(Date)
    last_visit_specialty: Mapped[str | None] = mapped_column(ForeignKey("specialties.code"))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (CheckConstraint("sex IN ('M','F')", name="ck_patient_sex"),)


class Offer(Base):
    """Optional add-on services (cross-sell / upsell). Shown only AFTER the care path is decided and
    confirmed, never on emergency/urgent paths, and never passed to the triage rules."""

    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)          # OFF-001
    kind: Mapped[str] = mapped_column(String(10))                          # cross_sell | upsell
    title_en: Mapped[str] = mapped_column(String(120))
    title_ar: Mapped[str] = mapped_column(String(120))
    description_en: Mapped[str] = mapped_column(String(300))
    description_ar: Mapped[str] = mapped_column(String(300))
    applicable_paths: Mapped[list[str]] = mapped_column(ARRAY(String(20)))
    specialty_code: Mapped[str | None] = mapped_column(ForeignKey("specialties.code"))
    requires_travel: Mapped[bool] = mapped_column(Boolean, default=False)  # only if the doctor is abroad
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (CheckConstraint("kind IN ('cross_sell','upsell')", name="ck_offer_kind"),)


class Booking(Base):
    """A confirmed appointment. The ticket number is generated by the DATABASE (sequence + default),
    so no application code, and certainly no LLM, can produce one.

    No patient identity is stored in this prototype: the ticket is what the patient presents.
    Production would link a verified patient record, with consent."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_number: Mapped[str] = mapped_column(
        String(20), unique=True,
        server_default=text("'HT-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('ticket_seq')::text, 6, '0')"),
    )
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"), unique=True)  # one booking per slot, enforced by DB
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.id"))
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))   # null = guest booking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    slot: Mapped[Slot] = relationship()
    doctor: Mapped[Doctor] = relationship()


class Notification(Base):
    """Transactional outbox. Rows are written in the SAME transaction as the booking, so a booking
    can't exist without its notifications, and a rolled-back booking notifies no one.
    A sender (here: a mock) delivers pending rows and retries failures.

    Privacy: notifications carry time + ticket only, never the patient's symptoms."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))       # doctor_email | hospital_system
    recipient: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("channel IN ('doctor_email','hospital_system')", name="ck_notification_channel"),
        CheckConstraint("status IN ('pending','sent','failed')", name="ck_notification_status"),
    )


class ChatSessionRow(Base):
    """Conversation state, so any serverless instance can continue any conversation.
    Holds the in-progress facts and chat text only; deleted after 30 minutes of inactivity.
    Identity numbers are never stored here (reception drops them after the check)."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), index=True)


class DailyUsage(Base):
    """Spend guard for the public demo: a hard cap on chat turns per day."""

    __tablename__ = "daily_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    chat_turns: Mapped[int] = mapped_column(Integer, default=0)


class RateHit(Base):
    """Rate-limit counter shared by every serverless instance (fixed one-minute windows)."""

    __tablename__ = "rate_hits"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
