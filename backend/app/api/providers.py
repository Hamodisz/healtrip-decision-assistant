from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import get_session
from app.schemas import DoctorOut, HospitalOut, SlotOut, SpecialtyOut

router = APIRouter(prefix="/api/v1", tags=["providers"])

DB = Annotated[Session, Depends(get_session)]
# Input is validated at the boundary: strict patterns, so junk never reaches a query.
SpecialtyQ = Annotated[str | None, Query(pattern=r"^[a-z_]{2,40}$")]
CountryQ = Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$", description="ISO alpha-2, e.g. SA")]
CityQ = Annotated[str | None, Query(min_length=2, max_length=60)]
LangQ = Annotated[str | None, Query(pattern=r"^[a-z]{2}$", description="ar | en | ...")]
DoctorId = Annotated[str, Path(pattern=r"^DOC-\d{3}$")]
HospitalId = Annotated[str, Path(pattern=r"^HOSP-\d{3}$")]


@router.get("/specialties", response_model=list[SpecialtyOut])
def specialties(db: DB):
    return repo.list_specialties(db)


@router.get("/hospitals", response_model=list[HospitalOut])
def hospitals(
    db: DB,
    country: CountryQ = None,
    city: CityQ = None,
    has_emergency: bool | None = None,
):
    return repo.search_hospitals(db, country=country, city=city, has_emergency=has_emergency)


@router.get("/hospitals/{hospital_id}", response_model=HospitalOut)
def hospital(hospital_id: HospitalId, db: DB):
    found = repo.get_hospital(db, hospital_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Hospital {hospital_id} not found")
    return found


@router.get("/doctors", response_model=list[DoctorOut])
def doctors(
    db: DB,
    specialty: SpecialtyQ = None,
    country: CountryQ = None,
    city: CityQ = None,
    language: LangQ = None,
    second_opinion: bool | None = None,
    remote_consult: bool | None = None,
):
    return repo.search_doctors(
        db,
        specialty=specialty,
        country=country,
        city=city,
        language=language,
        second_opinion=second_opinion,
        remote_consult=remote_consult,
    )


@router.get("/doctors/{doctor_id}", response_model=DoctorOut)
def doctor(doctor_id: DoctorId, db: DB):
    found = repo.get_doctor(db, doctor_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Doctor {doctor_id} not found")
    return found


@router.get("/doctors/{doctor_id}/slots", response_model=list[SlotOut])
def doctor_slots(
    doctor_id: DoctorId,
    db: DB,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    mode: Annotated[str | None, Query(pattern=r"^(in_person|remote)$")] = None,
):
    if not repo.get_doctor(db, doctor_id):
        raise HTTPException(status_code=404, detail=f"Doctor {doctor_id} not found")
    return repo.available_slots(db, doctor_id, days=days, mode=mode)
