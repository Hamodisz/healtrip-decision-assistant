"""Layer 2 of safety: deterministic care-path rules on STRUCTURED facts.

The LLM's only job (M3) is to fill `PatientFacts` from the conversation. The decision itself is
made here, in plain code: same facts in, same path out, every time, unit-tested line by line.
There is no LLM confidence score anywhere in this decision.

Rules follow the conservative direction of the 2021 AHA/ACC chest-pain guideline (acute chest
pain is handled as an emergency, not a clinic booking). PROTOTYPE RULES, not clinical guidance.
Only chest pain is modelled in detail; other complaints route to primary care after the red-flag check.
"""
from enum import Enum

from pydantic import BaseModel, Field


class Complaint(str, Enum):
    chest_pain = "chest_pain"
    other = "other"


class Onset(str, Enum):
    today = "today"                # started within the last 24 hours
    days = "days"                  # 1-7 days ago
    weeks_or_more = "weeks_or_more"


class CarePath(str, Enum):
    emergency = "emergency"            # go now / call 997; no provider booking
    urgent = "urgent"                  # same-day evaluation at an ER-capable hospital
    specialist = "specialist"
    second_opinion = "second_opinion"
    routine = "routine"                # primary care
    need_more_info = "need_more_info"


class PatientFacts(BaseModel):
    """What the agent extracts. None = not known yet (not asked, or not answered)."""

    chief_complaint: Complaint | None = None
    pain_now: bool | None = None
    onset: Onset | None = None
    shortness_of_breath: bool | None = None
    fainting_or_dizziness: bool | None = None
    sweating_or_nausea: bool | None = None
    pain_spreads: bool | None = None           # to arm, jaw, back or shoulder
    has_diagnosis_to_review: bool | None = None


class TriageResult(BaseModel):
    care_path: CarePath
    rule_id: str                                   # which rule fired: shown in logs and tests
    reason: str
    ask_next: list[str] = Field(default_factory=list)   # PatientFacts fields to ask about next
    specialty: str | None = None                   # for provider search, when the path allows it
    second_opinion: bool = False


# Fields asked together because any "yes" among them decides the path on its own.
_ASSOCIATED_SYMPTOMS = ["shortness_of_breath", "fainting_or_dizziness", "sweating_or_nausea", "pain_spreads"]


def decide(f: PatientFacts) -> TriageResult:
    """Rules are ordered: emergencies are checked first, and questions are asked only while
    an unknown fact could still change the path. That keeps the conversation short."""
    if f.chief_complaint is None:
        return TriageResult(care_path=CarePath.need_more_info, rule_id="Q_COMPLAINT",
                            reason="Main complaint not known yet", ask_next=["chief_complaint"])

    if f.chief_complaint is Complaint.other:
        return TriageResult(care_path=CarePath.routine, rule_id="R_OTHER",
                            reason="Not chest pain and no red flags: start with primary care",
                            specialty="family_medicine")

    # ── Chest pain: emergency rules first. Any single "yes" is enough. ──
    if f.pain_now:
        return TriageResult(care_path=CarePath.emergency, rule_id="E_PAIN_NOW",
                            reason="Chest pain happening now")
    flagged = [s for s in _ASSOCIATED_SYMPTOMS if getattr(f, s)]
    if flagged:
        return TriageResult(care_path=CarePath.emergency, rule_id="E_ASSOCIATED",
                            reason="Chest pain with " + ", ".join(flagged))

    # ── Not an emergency *yet*: ask what could still make it one. ──
    if f.pain_now is None:
        return TriageResult(care_path=CarePath.need_more_info, rule_id="Q_PAIN_NOW",
                            reason="Need to know if the pain is happening now", ask_next=["pain_now"])
    unknown = [s for s in _ASSOCIATED_SYMPTOMS if getattr(f, s) is None]
    if unknown:
        return TriageResult(care_path=CarePath.need_more_info, rule_id="Q_ASSOCIATED",
                            reason="Need to rule out associated warning symptoms", ask_next=unknown)
    if f.onset is None:
        return TriageResult(care_path=CarePath.need_more_info, rule_id="Q_ONSET",
                            reason="Need to know when the pain started", ask_next=["onset"])

    if f.onset is Onset.today:
        return TriageResult(care_path=CarePath.urgent, rule_id="U_RECENT",
                            reason="Chest pain that started in the last 24 hours needs same-day evaluation")

    if f.has_diagnosis_to_review is None:
        return TriageResult(care_path=CarePath.need_more_info, rule_id="Q_REVIEW",
                            reason="Need to know if there is an existing diagnosis to review",
                            ask_next=["has_diagnosis_to_review"])
    if f.has_diagnosis_to_review:
        return TriageResult(care_path=CarePath.second_opinion, rule_id="S_REVIEW",
                            reason="Existing diagnosis or plan to be reviewed, no warning symptoms",
                            specialty="cardiology", second_opinion=True)

    return TriageResult(care_path=CarePath.specialist, rule_id="S_CARDIOLOGY",
                        reason="Past or recurring chest pain without warning symptoms: cardiology assessment",
                        specialty="cardiology")


# Fixed, reviewed text: never generated by the LLM.
EMERGENCY_MESSAGE = {
    "en": ("Based on what you've described, this may need urgent medical attention. "
           "Please seek emergency care now: call 997 (Red Crescent ambulance) or 911 where available, "
           "or go to the nearest emergency department. Do not wait for a specialist appointment. "
           "This assistant cannot diagnose your condition."),
    "ar": ("بناءً على ما وصفته، قد تحتاج حالتك إلى رعاية طبية عاجلة. "
           "يُرجى طلب الرعاية الطارئة الآن: اتصل بالرقم 997 (الهلال الأحمر) أو 911 حيث يتوفر، "
           "أو توجّه إلى أقرب قسم طوارئ. لا تنتظر موعدًا مع طبيب مختص. "
           "هذا المساعد لا يستطيع تشخيص حالتك."),
}
URGENT_MESSAGE = {
    "en": ("Chest pain that started today should be checked by a doctor today. Please go to a hospital "
           "emergency department today. If the pain returns or gets worse, call 997 immediately."),
    "ar": ("ألم الصدر الذي بدأ اليوم يجب أن يفحصه طبيب اليوم. يُرجى التوجه إلى قسم الطوارئ في مستشفى اليوم. "
           "إذا عاد الألم أو ازداد، اتصل بالرقم 997 فورًا."),
}


class Assessment(BaseModel):
    red_flags: list[str]
    triage: TriageResult
    safety_message: str | None = None   # fixed text, set only for emergency / urgent


def assess(message: str | None, facts: PatientFacts, lang: str = "en") -> Assessment:
    """Both safety layers together. A raw-text red flag overrides everything else."""
    from app import red_flags  # local import keeps triage.decide() pure and dependency-free

    hits = red_flags.check(message) if message else []
    if hits:
        result = TriageResult(care_path=CarePath.emergency, rule_id=hits[0].id,
                              reason="Red flag in patient's words: " + hits[0].description)
    else:
        result = decide(facts)

    msg = None
    if result.care_path is CarePath.emergency:
        msg = EMERGENCY_MESSAGE[lang]
    elif result.care_path is CarePath.urgent:
        msg = URGENT_MESSAGE[lang]
    return Assessment(red_flags=[h.id for h in hits], triage=result, safety_message=msg)
