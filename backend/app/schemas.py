"""API response contracts. The frontend (and later the AI's provider cards) render from these,
built from DB rows, never from model-generated text."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SpecialtyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name_en: str
    name_ar: str


class HospitalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name_en: str
    name_ar: str
    country: str
    city: str
    timezone: str
    has_emergency: bool
    accreditation: str | None
    languages: list[str]
    is_mock: bool


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name_en: str
    name_ar: str
    specialty: SpecialtyOut
    hospital: HospitalOut
    languages: list[str]
    years_experience: int
    offers_second_opinion: bool
    offers_remote_consult: bool
    consultation_fee: float
    currency: str
    is_mock: bool


class SlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: str
    starts_at: datetime
    duration_min: int
    mode: str


class ErrorOut(BaseModel):
    code: str
    message: str
