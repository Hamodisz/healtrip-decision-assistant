"""Reception: find the patient's file, and describe it back to them.

Identity: a file number (or national ID) alone is not enough. It must match the date of birth.
A failed check never reveals whether the file exists, and 3 failures end the attempt (the patient
continues as a guest). Production would use real identity verification (e.g. Nafath / OTP).

The summary the patient hears is built HERE, from DB fields, by fixed templates. The LLM never
paraphrases a medical record: a paraphrase is exactly where a condition gets invented or dropped.
"""
import re
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Patient, Specialty
from app.triage import Complaint, Onset, PatientFacts

MAX_VERIFY_ATTEMPTS = 3
FILE_RE, NID_RE = re.compile(r"^MRN-\d{6}$"), re.compile(r"^\d{10}$")


def verify(db: Session, identifier: str, dob: date) -> Patient | None:
    identifier = identifier.strip().upper()
    if not (FILE_RE.match(identifier) or NID_RE.match(identifier)):
        return None
    p = db.scalars(select(Patient).where(or_(Patient.file_number == identifier,
                                             Patient.national_id == identifier))).one_or_none()
    return p if p and p.date_of_birth == dob else None  # same result for "no such file" and "wrong DOB"


def greeting_name(p: Patient, lang: str) -> str:
    first = (p.name_ar if lang == "ar" else p.name_en).split()[0]
    if lang == "ar":
        return ("السيد " if p.sex == "M" else "السيدة ") + first
    return ("Mr. " if p.sex == "M" else "Ms. ") + first


def file_summary(db: Session, p: Patient, lang: str) -> str:
    ar = lang == "ar"
    none = "لا يوجد" if ar else "none recorded"
    sep = "، " if ar else ", "
    cond = sep.join(p.known_conditions) if p.known_conditions else none
    alg = sep.join(p.allergies) if p.allergies else none
    if p.last_visit_date:
        spec = db.get(Specialty, p.last_visit_specialty) if p.last_visit_specialty else None
        spec_name = (spec.name_ar if ar else spec.name_en) if spec else ""
        visit = f"{p.last_visit_date.isoformat()} ({spec_name})" if spec_name else p.last_visit_date.isoformat()
    else:
        visit = none
    if ar:
        return f"من ملفك لدينا: الحالات المسجلة: {cond}. الحساسية: {alg}. آخر زيارة: {visit}."
    return f"From your file: known conditions: {cond}. Allergies: {alg}. Last visit: {visit}."


_ONSET = {Onset.today: ("started today", "بدأ اليوم"), Onset.days: ("started in the last few days", "بدأ خلال الأيام الماضية"),
          Onset.weeks_or_more: ("started weeks ago or longer", "بدأ منذ أسابيع أو أكثر")}


def facts_summary(f: PatientFacts, lang: str) -> str:
    """What the patient told us today, from structured facts only."""
    ar = lang == "ar"
    parts = []
    if f.chief_complaint is Complaint.chest_pain:
        parts.append("ألم في الصدر" if ar else "chest pain")
        if f.pain_now is False:
            parts.append("غير موجود الآن" if ar else "not happening right now")
        if f.onset:
            parts.append(_ONSET[f.onset][1 if ar else 0])
        if all(getattr(f, k) is False for k in ("shortness_of_breath", "fainting_or_dizziness", "sweating_or_nausea", "pain_spreads")):
            parts.append("بدون ضيق تنفس أو إغماء أو تعرق أو ألم منتشر" if ar
                         else "no shortness of breath, fainting, sweating or spreading pain")
    if f.has_diagnosis_to_review:
        parts.append("وتريد رأيًا ثانيًا في تشخيص سابق" if ar else "you'd like a second opinion on an existing diagnosis")
    elif f.has_diagnosis_to_review is False:
        parts.append("بدون تشخيص سابق" if ar else "no previous diagnosis")
    joined = "، ".join(parts) if ar else ", ".join(parts)
    return (f"ومما ذكرته اليوم: {joined}." if ar else f"From what you told me today: {joined}.")
