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
from app.models import Doctor, Hospital, Offer, Patient, Slot, Specialty

SPECIALTIES = [
    ("cardiology", "Cardiology", "أمراض القلب",
     "Heart and blood-vessel conditions: chest pain evaluation, rhythm problems, heart failure."),
    ("cardiac_surgery", "Cardiac Surgery", "جراحة القلب",
     "Surgical treatment of the heart: bypass, valve repair/replacement."),
    ("emergency_medicine", "Emergency Medicine", "طب الطوارئ",
     "Immediate care for acute, possibly life-threatening conditions. Walk-in, not booked."),
    ("internal_medicine", "Internal Medicine", "الطب الباطني",
     "Adult general medicine: diagnosis of multi-system and unclear symptoms."),
    ("family_medicine", "Family Medicine", "طب الأسرة",
     "First point of contact for routine care, screening and referrals."),
    ("pulmonology", "Pulmonology", "أمراض الصدر",
     "Lungs and breathing: asthma, COPD, breathlessness."),
    ("gastroenterology", "Gastroenterology", "الجهاز الهضمي",
     "Digestive system: reflux, stomach, bowel and liver conditions."),
    ("orthopedics", "Orthopedics", "العظام",
     "Bones, joints and muscles, including musculoskeletal chest-wall pain."),
    ("neurology", "Neurology", "الأعصاب",
     "Brain, nerves and spine: headaches, numbness, seizures."),
]

# id, name_en, name_ar, country, city, address, tz, has_er, accreditation, languages
HOSPITALS = [
    ("HOSP-001", "Demo Riyadh Heart Institute", "معهد الرياض للقلب (تجريبي)", "SA", "Riyadh", "12 Demo King Fahd Rd, Riyadh", "Asia/Riyadh", True, "JCI", ["ar", "en"]),
    ("HOSP-002", "Demo Riyadh General Hospital", "مستشفى الرياض العام (تجريبي)", "SA", "Riyadh", "45 Demo Olaya St, Riyadh", "Asia/Riyadh", True, "CBAHI", ["ar", "en"]),
    ("HOSP-003", "Demo Jeddah Coastal Medical Center", "مركز جدة الساحلي الطبي (تجريبي)", "SA", "Jeddah", "8 Demo Corniche Rd, Jeddah", "Asia/Riyadh", True, "JCI", ["ar", "en"]),
    ("HOSP-004", "Demo Dubai Specialty Clinic", "عيادة دبي التخصصية (تجريبي)", "AE", "Dubai", "Demo Healthcare City, Bldg 3, Dubai", "Asia/Dubai", False, "JCI", ["ar", "en"]),
    ("HOSP-005", "Demo Istanbul University Hospital", "مستشفى إسطنبول الجامعي (تجريبي)", "TR", "Istanbul", "101 Demo Bagdat Ave, Istanbul", "Europe/Istanbul", True, "JCI", ["tr", "en", "ar"]),
    ("HOSP-006", "Demo Berlin Second-Opinion Center", "مركز برلين للرأي الثاني (تجريبي)", "DE", "Berlin", "7 Demo Charite Str, Berlin", "Europe/Berlin", False, None, ["de", "en", "ar"]),
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

# Fictional patients. file_number, national_id, dob, name_en, name_ar, sex, city, lang, conditions, allergies, last visit, specialty
PATIENTS = [
    ("MRN-100001", "1000000001", date(1971, 4, 12), "Ahmed Al-Qurashi", "أحمد القرشي", "M", "Riyadh", "ar",
     ["Hypertension", "Type 2 diabetes"], ["Penicillin"], date(2026, 3, 2), "internal_medicine"),
    ("MRN-100002", "1000000002", date(1985, 9, 30), "Sara Al-Mutairi", "سارة المطيري", "F", "Jeddah", "ar",
     [], [], date(2025, 11, 18), "family_medicine"),
    ("MRN-100003", "2000000003", date(1962, 1, 5), "John Miller", "جون ميلر", "M", "Riyadh", "en",
     ["Mitral valve regurgitation"], ["Aspirin"], date(2026, 8, 20), "cardiology"),
    ("MRN-100004", "1000000004", date(1994, 6, 21), "Noura Al-Ghamdi", "نورة الغامدي", "F", "Riyadh", "ar",
     ["Asthma"], [], None, None),
]

# id, kind, title_en, title_ar, desc_en, desc_ar, paths, specialty, requires_travel, price, currency
OFFERS = [
    ("OFF-001", "cross_sell", "Cardiac check-up package", "باقة فحص القلب",
     "ECG, echocardiogram and lipid panel on the same visit.", "تخطيط قلب وإيكو وتحليل دهون في نفس الزيارة.",
     ["specialist", "second_opinion"], "cardiology", False, 950, "SAR"),
    ("OFF-002", "cross_sell", "Medical report translation", "ترجمة التقارير الطبية",
     "Certified translation of your reports (Arabic ⇄ English / German / Turkish).", "ترجمة معتمدة لتقاريرك (عربي ⇄ إنجليزي / ألماني / تركي).",
     ["second_opinion"], None, False, 300, "SAR"),
    ("OFF-003", "cross_sell", "Travel & visa package", "باقة السفر والتأشيرة",
     "Visa support, hotel near the hospital and airport transfer.", "دعم التأشيرة وفندق قريب من المستشفى وتوصيل من المطار.",
     ["specialist", "second_opinion"], None, True, 2500, "SAR"),
    ("OFF-004", "upsell", "Priority remote review (48h)", "مراجعة عن بُعد بأولوية (48 ساعة)",
     "Your records reviewed by the doctor within 48 hours instead of 7 days.", "مراجعة ملفاتك من الطبيب خلال 48 ساعة بدلاً من 7 أيام.",
     ["second_opinion"], None, False, 700, "SAR"),
    ("OFF-005", "upsell", "VIP concierge", "خدمة كبار الشخصيات",
     "Dedicated coordinator, private transfer and companion accommodation.", "منسق خاص وتوصيل خاص وسكن للمرافق.",
     ["specialist", "second_opinion"], None, True, 4000, "SAR"),
    ("OFF-006", "cross_sell", "Annual health check", "الفحص الصحي السنوي",
     "Comprehensive yearly screening with your family doctor.", "فحص شامل سنوي مع طبيب الأسرة.",
     ["routine"], "family_medicine", False, 600, "SAR"),
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
        country, tz = hospitals[hosp_id][3], ZoneInfo(hospitals[hosp_id][6])
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
    with engine.begin() as conn:
        conn.execute(text("DROP SEQUENCE IF EXISTS ticket_seq"))
        conn.execute(text("CREATE SEQUENCE ticket_seq"))
    Base.metadata.create_all(engine)


def seed(start: date | None = None) -> dict[str, int]:
    start = start or date.today()
    hospitals = {h[0]: h for h in HOSPITALS}
    with SessionLocal.begin() as s:
        s.add_all(Specialty(code=c, name_en=en, name_ar=ar, description=d) for c, en, ar, d in SPECIALTIES)
        s.add_all(
            Hospital(id=h[0], name_en=h[1], name_ar=h[2], country=h[3], city=h[4], address=h[5],
                     timezone=h[6], has_emergency=h[7], accreditation=h[8], languages=h[9])
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
        s.add_all(Patient(file_number=p[0], national_id=p[1], date_of_birth=p[2], name_en=p[3], name_ar=p[4], sex=p[5],
                          city=p[6], preferred_language=p[7], known_conditions=p[8], allergies=p[9],
                          last_visit_date=p[10], last_visit_specialty=p[11]) for p in PATIENTS)
        s.add_all(Offer(id=o[0], kind=o[1], title_en=o[2], title_ar=o[3], description_en=o[4], description_ar=o[5],
                        applicable_paths=o[6], specialty_code=o[7], requires_travel=o[8], price=o[9], currency=o[10])
                  for o in OFFERS)
    return {"patients": len(PATIENTS), "offers": len(OFFERS), "specialties": len(SPECIALTIES), "hospitals": len(HOSPITALS), "doctors": len(DOCTORS), "slots": len(slots)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    args = parser.parse_args()
    if args.reset:
        reset_schema()
    print(seed())
