"""Database schema.

IDs for hospitals and doctors are human-readable strings (HOSP-001, DOC-001) on purpose:
the AI may only refer to a provider by an ID that a tool returned, and readable IDs make
that grounding rule easy to audit in logs and tests.
"""
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
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
