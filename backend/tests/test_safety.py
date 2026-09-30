"""Safety rules are the most important code in the project, so they are tested as a table:
each row is a patient situation and the path it MUST produce."""
import pytest

from app import red_flags
from app.triage import CarePath, Complaint, Onset, PatientFacts, assess, decide

NO_SYMPTOMS = dict(shortness_of_breath=False, fainting_or_dizziness=False, sweating_or_nausea=False, pain_spreads=False)
CP = Complaint.chest_pain


@pytest.mark.parametrize(
    "facts, path, rule",
    [
        # Emergencies: any single one is enough, even with everything else unknown
        (dict(chief_complaint=CP, pain_now=True), CarePath.emergency, "E_PAIN_NOW"),
        (dict(chief_complaint=CP, shortness_of_breath=True), CarePath.emergency, "E_ASSOCIATED"),
        (dict(chief_complaint=CP, pain_now=False, fainting_or_dizziness=True), CarePath.emergency, "E_ASSOCIATED"),
        (dict(chief_complaint=CP, pain_now=False, pain_spreads=True, onset=Onset.weeks_or_more), CarePath.emergency, "E_ASSOCIATED"),
        # Recent pain, now gone, no symptoms: same-day, not a booking next week
        (dict(chief_complaint=CP, pain_now=False, onset=Onset.today, **NO_SYMPTOMS), CarePath.urgent, "U_RECENT"),
        # Older pain, no symptoms
        (dict(chief_complaint=CP, pain_now=False, onset=Onset.weeks_or_more, has_diagnosis_to_review=False, **NO_SYMPTOMS), CarePath.specialist, "S_CARDIOLOGY"),
        (dict(chief_complaint=CP, pain_now=False, onset=Onset.days, has_diagnosis_to_review=True, **NO_SYMPTOMS), CarePath.second_opinion, "S_REVIEW"),
        # Not chest pain
        (dict(chief_complaint=Complaint.other), CarePath.routine, "R_OTHER"),
    ],
)
def test_care_path(facts, path, rule):
    r = decide(PatientFacts(**facts))
    assert (r.care_path, r.rule_id) == (path, rule)


def test_a_second_opinion_never_overrides_an_emergency():
    r = decide(PatientFacts(chief_complaint=CP, pain_now=True, has_diagnosis_to_review=True))
    assert r.care_path is CarePath.emergency


@pytest.mark.parametrize(
    "facts, ask",
    [
        (dict(), ["chief_complaint"]),
        (dict(chief_complaint=CP), ["pain_now"]),  # the most important question comes first
        (dict(chief_complaint=CP, pain_now=False), ["shortness_of_breath", "fainting_or_dizziness", "sweating_or_nausea", "pain_spreads"]),
        (dict(chief_complaint=CP, pain_now=False, **NO_SYMPTOMS), ["onset"]),
        (dict(chief_complaint=CP, pain_now=False, onset=Onset.days, **NO_SYMPTOMS), ["has_diagnosis_to_review"]),
    ],
)
def test_asks_only_what_can_still_change_the_decision(facts, ask):
    r = decide(PatientFacts(**facts))
    assert r.care_path is CarePath.need_more_info and r.ask_next == ask


def test_no_questions_after_an_emergency_is_found():
    assert decide(PatientFacts(chief_complaint=CP, pain_now=True)).ask_next == []


def test_provider_search_is_only_enabled_for_non_urgent_paths():
    assert decide(PatientFacts(chief_complaint=CP, pain_now=True)).specialty is None
    assert decide(PatientFacts(chief_complaint=CP, pain_now=False, onset=Onset.today, **NO_SYMPTOMS)).specialty is None


def test_same_facts_same_answer():
    f = PatientFacts(chief_complaint=CP, pain_now=False, onset=Onset.days, has_diagnosis_to_review=False, **NO_SYMPTOMS)
    assert len({decide(f).model_dump_json() for _ in range(50)}) == 1


# ── Red-flag phrases on raw text, Arabic + English ──
@pytest.mark.parametrize(
    "text, flag",
    [
        ("I have chest pain and I can't breathe", "RF_BREATHING"),
        ("my husband just passed out", "RF_FAINTING"),
        ("crushing chest pain since an hour", "RF_CRUSHING_CHEST"),
        ("her face is drooping and slurred speech", "RF_STROKE_SIGNS"),
        ("عندي ألم في صدري وما أقدر أتنفس", "RF_BREATHING"),
        ("أُغمي عليّ قبل شوي", "RF_FAINTING"),
        ("ألم شديد في الصدر", "RF_CRUSHING_CHEST"),
        ("احس بضغط قوي على الصدر", "RF_CRUSHING_CHEST"),
        ("ضيق نفس شديد", "RF_BREATHING"),
    ],
)
def test_red_flag_detected(text, flag):
    assert flag in [f.id for f in red_flags.check(text)]


@pytest.mark.parametrize(
    "text",
    [
        "I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion.",
        "I had mild chest pain last month, it went away",
        "عندي ألم في الصدر من أسبوعين وأبغى رأي طبي ثاني",
    ],
)
def test_ordinary_messages_do_not_trigger_red_flags(text):
    # These still go through the question flow: not flagged here does NOT mean safe.
    assert red_flags.check(text) == []


def test_red_flag_in_words_overrides_facts_and_returns_fixed_message():
    a = assess("والله ما اقدر اتنفس", PatientFacts(chief_complaint=Complaint.other), lang="ar")
    assert a.triage.care_path is CarePath.emergency
    assert "997" in a.safety_message and "لا يستطيع تشخيص" in a.safety_message


def test_triage_endpoint(client):
    r = client.post("/api/v1/triage", json={"message": "chest pain", "facts": {"chief_complaint": "chest_pain", "pain_now": True}})
    body = r.json()
    assert r.status_code == 200
    assert body["triage"]["care_path"] == "emergency" and "997" in body["safety_message"]
    assert client.post("/api/v1/triage", json={"facts": {"pain_now": "maybe"}}).status_code == 422
    assert client.post("/api/v1/triage", json={"message": "x" * 2001}).status_code == 422


def test_second_opinion_without_chest_pain():
    # Live-run regression: "my cardiologist diagnosed a valve problem, I want a second opinion"
    # was asked "what is the main concern?" 3 times and then sent to primary care.
    f = PatientFacts(chief_complaint=Complaint.other, has_diagnosis_to_review=True)
    assert decide(f).ask_next == ["review_specialty"]
    r = decide(PatientFacts(chief_complaint=Complaint.other, has_diagnosis_to_review=True, review_specialty="cardiology"))
    assert (r.care_path, r.specialty, r.second_opinion) == (CarePath.second_opinion, "cardiology", True)
