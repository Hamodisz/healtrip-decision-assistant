"""The ONLY place provider data is queried.

Both the REST API and (from M3) the AI agent's tools call these functions, so the model can
never see data the API wouldn't return, and there is exactly one set of queries to secure.
All filters are bound parameters via SQLAlchemy; no string-built SQL anywhere.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Doctor, Hospital, Slot, Specialty

MAX_RESULTS = 20  # hard cap: protects the DB and keeps tool results small enough for the model


def list_specialties(session: Session) -> list[Specialty]:
    return list(session.scalars(select(Specialty).order_by(Specialty.code)))


def search_hospitals(
    session: Session,
    *,
    country: str | None = None,
    city: str | None = None,
    has_emergency: bool | None = None,
    limit: int = MAX_RESULTS,
) -> list[Hospital]:
    stmt = select(Hospital)
    if country:
        stmt = stmt.where(Hospital.country == country.upper())
    if city:
        stmt = stmt.where(Hospital.city.ilike(city))
    if has_emergency is not None:
        stmt = stmt.where(Hospital.has_emergency == has_emergency)
    return list(session.scalars(stmt.order_by(Hospital.id).limit(min(limit, MAX_RESULTS))))


def search_doctors(
    session: Session,
    *,
    specialty: str | None = None,
    country: str | None = None,
    city: str | None = None,
    language: str | None = None,
    second_opinion: bool | None = None,
    remote_consult: bool | None = None,
    limit: int = MAX_RESULTS,
) -> list[Doctor]:
    stmt = select(Doctor).join(Doctor.hospital).options(
        joinedload(Doctor.hospital), joinedload(Doctor.specialty)
    )
    if specialty:
        stmt = stmt.where(Doctor.specialty_code == specialty)
    if country:
        stmt = stmt.where(Hospital.country == country.upper())
    if city:
        stmt = stmt.where(Hospital.city.ilike(city))
    if language:
        stmt = stmt.where(Doctor.languages.any(language))
    if second_opinion is not None:
        stmt = stmt.where(Doctor.offers_second_opinion == second_opinion)
    if remote_consult is not None:
        stmt = stmt.where(Doctor.offers_remote_consult == remote_consult)
    stmt = stmt.order_by(Doctor.years_experience.desc(), Doctor.id).limit(min(limit, MAX_RESULTS))
    return list(session.scalars(stmt).unique())


def get_hospital(session: Session, hospital_id: str) -> Hospital | None:
    return session.get(Hospital, hospital_id)


def get_doctor(session: Session, doctor_id: str) -> Doctor | None:
    stmt = (
        select(Doctor)
        .where(Doctor.id == doctor_id)
        .options(joinedload(Doctor.hospital), joinedload(Doctor.specialty))
    )
    return session.scalars(stmt).unique().one_or_none()


def available_slots(
    session: Session,
    doctor_id: str,
    *,
    days: int = 7,
    mode: str | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[Slot]:
    """Open slots in the next `days`. A hold whose time has passed counts as open again."""
    now = now or datetime.now(timezone.utc)
    stmt = select(Slot).where(
        Slot.doctor_id == doctor_id,
        Slot.starts_at > now,
        Slot.starts_at < now + timedelta(days=days),
        or_(Slot.status == "open", and_(Slot.status == "held", Slot.held_until < now)),
    )
    if mode:
        stmt = stmt.where(Slot.mode == mode)
    return list(session.scalars(stmt.order_by(Slot.starts_at).limit(min(limit, MAX_RESULTS))))
