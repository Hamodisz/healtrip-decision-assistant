"""Seed a small, clearly FICTIONAL cross-border provider network.

Usage:  python -m app.seed --reset

Design notes:
- Every row is is_mock=True and hospital names carry a "Demo" prefix so nothing can be
  mistaken for a real institution.
- Emergency-medicine doctors get NO appointment slots: an ER is walk-in. The ER route in the
  decision engine resolves to hospitals with has_emergency=True, never to a booking.
- Slots are generated in each hospital's LOCAL timezone, on that country's working days,
  relative to the day the seed runs, so the demo always has future availability.
"""
import argparse
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import Base, SessionLocal, engine
from app.models import Doctor, Hospital, Slot, Specialty

SPECIALTIES = [
    ("cardiology", "Cardiology", "أمراض القلب"),
    ("cardiac_surgery", "Cardiac Surgery", "جراحة القلب"),
    ("emergency_medicine", "Emergency Medicine", "طب الطوارئ"),
    ("internal_medicine", "Internal Medicine", "الطب الباطني"),
    ("family_medicine", "Family Medicine", "طب الأسرة"),
    ("pulmonology", "Pulmonology", "أمراض الصدر"),
    ("gastroenterology", "Gastroenterology", "الجهاز الهضمي"),
    ("orthopedics", "Orthopedics", "العظام"),
    ("neurology", "Neurology", "الأعصاب"),
]

# id, name_en, name_ar, country, city, tz, has_er, accreditation, languages
HOSPITALS = [
    ("HOSP-001", "Demo Riyadh Heart Institute", "معهد الرياض للقلب (تجريبي)", "SA", "Riyadh", "Asia/Riyadh", True, "JCI", ["ar", "en"]),
    ("HOSP-002", "Demo Riyadh General Hospital", "مستشفى الرياض العام (تجريبي)", "SA", "Riyadh", "Asia/Riyadh", True, "CBAHI", ["ar", "en"]),
    ("HOSP-003", "Demo Jeddah Coastal Medical Center", "مركز جدة الساحلي الطبي (تجريبي)", "SA", "Jeddah", "Asia/Riyadh", True, "JCI", ["ar", "en"]),
    ("HOSP-004", "Demo Dubai Specialty Clinic", "عيادة دبي التخصصية (تجريبي)", "AE", "Dubai", "Asia/Dubai", False, "JCI", ["ar", "en"]),
    ("HOSP-005", "Demo Istanbul University Hospital", "مستشفى إسطنبول الجامعي (تجريبي)", "TR", "Istanbul", "Europe/Istanbul", True, "JCI", ["tr", "en", "ar"]),
    ("HOSP-006", "Demo Berlin Second-Opinion Center", "مركز برلين للرأي الثاني (تجريبي)", "DE", "Berlin", "Europe/Berlin", False, None, ["de", "en", "ar"]),
]

# id, name_en, name_ar, specialty, hospital, languages, years, second_opinion, remote, fee, currency
DOCTORS = [
    ("DOC-001", "Dr. Faisal Al-Harbi", "د. فيصل الحربي", "cardiology", "HOSP-001", ["ar", "en"], 18, True, True, 600, "SAR"),
    ("DOC-002", "Dr. Nora Al-Zahrani", "د. نورة الزهراني", "cardiology", "HOSP-003", ["ar", "en"], 11, False, False, 450, "SAR"),
    ("DOC-003", "Dr. Emre Yildiz", "د. أمره يلدز", "cardiology", "HOSP-005", ["tr", "en"], 15, True, True, 150, "USD"),
    ("DOC-004", "Dr. Katrin Weber", "د. كاترين فيبر", "cardiology", "HOSP-006", ["de", "en", "ar"], 22, True, True, 250, "EUR"),
    ("DOC-005", "Dr. Khalid Al-Otaibi", "د. خالد العتيبي", "emergency_medicine", "HOSP-002", ["ar", "en"], 9, False, False, 0, "SAR"),
    ("DOC-006", "Dr. Reem Bakr", "د. ريم بكر", "emergency_medicine", "HOSP-003", ["ar", "en"], 7, False, False, 0, "SAR"),
    ("DOC-007", "Dr. Omar Al-Qahtani", "د. عمر القحطاني", "internal_medicine", "HOSP-002", ["ar", "en"], 14, False, True, 300, "SAR"),
    ("DOC-008", "Dr. Layla Haddad", "د. ليلى حداد", "internal_medicine", "HOSP-004", ["ar", "en"], 12, True, True, 500, "AED"),
    ("DOC-009", "Dr. Sultan Al-Dosari", "د. سلطان الدوسري", "family_medicine", "HOSP-002", ["ar", "en"], 8, False, True, 200, "SAR"),
    ("DOC-010", "Dr. Hana Mansour", "د. هناء منصور", "family_medicine", "HOSP-004", ["ar", "en"], 10, False, True, 350, "AED"),
    ("DOC-011", "Dr. Yousef Al-Shehri", "د. يوسف الشهري", "pulmonology", "HOSP-001", ["ar", "en"], 16, True, False, 550, "SAR"),
    ("DOC-012", "Dr. Samir Khoury", "د. سمير خوري", "gastroenterology", "HOSP-004", ["ar", "en"], 13, True, True, 600, "AED"),
    ("DOC-013", "Dr. Ayse Demir", "د. عائشة دمير", "orthopedics", "HOSP-005", ["tr", "en", "ar"], 17, True, True, 140, "USD"),
    ("DOC-014", "Dr. Jonas Richter", "د. يوناس ريشتر", "neurology", "HOSP-006", ["de", "en"], 20, True, True, 280, "EUR"),
    ("DOC-015", "Dr. Mehmet Arslan", "د. محمد أرسلان", "cardiac_surgery", "HOSP-005", ["tr", "en", "ar"], 24, True, True, 200, "USD"),
]

# Weekend days per country (Python weekday(): Mon=0 ... Sun=6)
WEEKEND = {"SA": {4, 5}, "AE": {5, 6}, "TR": {5, 6}, "DE": {5, 6}}
IN_PERSON_TIMES = [time(9), time(10), time(11), time(14), time(15)]
REMOTE_TIME = time(17)
SLOT_DAYS = 14


def build_slots(doctors: list[tuple], hospitals: dict[str, tuple], start: date) -> list[Slot]:
    slots = []
    for d in doctors:
        doc_id, specialty, hosp_id, remote = d[0], d[3], d[4], d[8]
        if specialty == "emergency_medicine":
            continue  # ER is walk-in: no appointments
        country, tz = hospitals[hosp_id][3], ZoneInfo(hospitals[hosp_id][5])
        for offset in range(1, SLOT_DAYS + 1):
            day = start + timedelta(days=offset)
            if day.weekday() in WEEKEND[country]:
                continue
            for t in IN_PERSON_TIMES:
                slots.append(Slot(doctor_id=doc_id, starts_at=datetime.combine(day, t, tz), mode="in_person"))
            if remote:
                slots.append(Slot(doctor_id=doc_id, starts_at=datetime.combine(day, REMOTE_TIME, tz), mode="remote"))
    return slots


def reset_schema() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # for RAG (M7)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed(start: date | None = None) -> dict[str, int]:
    start = start or date.today()
    hospitals = {h[0]: h for h in HOSPITALS}
    with SessionLocal.begin() as s:
        s.add_all(Specialty(code=c, name_en=en, name_ar=ar) for c, en, ar in SPECIALTIES)
        s.add_all(
            Hospital(id=h[0], name_en=h[1], name_ar=h[2], country=h[3], city=h[4], timezone=h[5],
                     has_emergency=h[6], accreditation=h[7], languages=h[8])
            for h in HOSPITALS
        )
        s.flush()
        s.add_all(
            Doctor(id=d[0], name_en=d[1], name_ar=d[2], specialty_code=d[3], hospital_id=d[4],
                   languages=d[5], years_experience=d[6], offers_second_opinion=d[7],
                   offers_remote_consult=d[8], consultation_fee=d[9], currency=d[10])
            for d in DOCTORS
        )
        s.flush()
        slots = build_slots(DOCTORS, hospitals, start)
        s.add_all(slots)
    return {"specialties": len(SPECIALTIES), "hospitals": len(HOSPITALS), "doctors": len(DOCTORS), "slots": len(slots)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    args = parser.parse_args()
    if args.reset:
        reset_schema()
    print(seed())
