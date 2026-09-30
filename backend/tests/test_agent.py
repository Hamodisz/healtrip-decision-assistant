"""Agent tests with a scripted fake LLM: no network, no cost, and failure cases on demand.
Each test pins down one answer to 'what happens when...'."""
import json

import pytest
from sqlalchemy.exc import OperationalError

from app import agent, repository
from app.agent import ChatSession, run_turn
from app.api import chat as chat_api
from app.db import SessionLocal
from app.llm import LLMUnavailable
from app.main import app
from app.tools import ToolGateway
from app.triage import CarePath, TriageResult


def facts(**kw):
    return {"tool_calls": [{"id": "x", "type": "function",
                            "function": {"name": "record_facts", "arguments": json.dumps(kw)}}]}


def tool(name, **kw):
    return {"content": None, "tool_calls": [{"id": f"c-{name}", "type": "function",
                                             "function": {"name": name, "arguments": json.dumps(kw)}}]}


def text(t):
    return {"content": t}


class FakeLLM:
    """Returns scripted responses in order; fails the test if called more than scripted."""

    def __init__(self, *script):
        self.script, self.calls = list(script), 0

    def chat(self, messages, tools=None, tool_choice=None):
        self.calls += 1
        if not self.script:
            raise AssertionError("LLM called but nothing was scripted (it should not have been called)")
        nxt = self.script.pop(0)
        if nxt is LLMUnavailable:
            raise LLMUnavailable()
        return nxt


NO_SYMPTOMS = dict(shortness_of_breath=False, fainting_or_dizziness=False, sweating_or_nausea=False, pain_spreads=False)


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


def test_full_workflow_reception_file_triage_handoff_confirm_recommend(db):
    """The hospital-reception workflow end to end (patient MRN-100001 is fictional seed data)."""
    s = ChatSession()
    llm = FakeLLM(
        facts(chief_complaint="chest_pain"), text("Is the pain happening right now?"),          # 1 safety first
        facts(pain_now=False), text("Do you have a file with us?"),                           # 2 reception
        facts(has_file=True, file_number="MRN-100001"), text("What is your date of birth?"),   # 3 verify
        facts(date_of_birth="1971-04-12"), text("Any shortness of breath, fainting, sweating or spreading pain?"),  # 4 file found
        facts(**NO_SYMPTOMS), text("When did it start?"),
        facts(onset="weeks_or_more", city="Riyadh"), text("Do you have a diagnosis to review?"),
        facts(has_diagnosis_to_review=False),                                                   # 7 → handoff (template, no LLM)
        facts(summary_confirmed=True), tool("search_providers", city="Riyadh"),                 # 8 confirmed → doctors
        text("A cardiologist is the right next step: [DOC-001]."),
    )
    msgs = ["I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion.",
            "no", "yes, MRN-100001", "12 April 1971", "none of those", "a few weeks ago, I'm in Riyadh", "no", "yes that's correct"]
    r = [run_turn(db, llm, s, m) for m in msgs]

    assert r[0].triage.rule_id == "Q_PAIN_NOW"                       # never ask for a file before this
    assert [x.agent for x in r[1:3]] == ["reception", "reception"]
    assert r[3].reply.startswith("Thank you, Mr. Ahmed. I found your file.")
    handoff = r[6]
    assert handoff.agent == "clinic_assistant" and handoff.clinic == "Cardiology"
    assert "Hello Mr. Ahmed" in handoff.reply and "Hypertension" in handoff.reply and "Penicillin" in handoff.reply  # from the DB file
    assert "Is this correct?" in handoff.reply and handoff.providers == []    # nothing offered before confirmation
    last = r[7]
    assert [p["doctor_id"] for p in last.providers] == ["DOC-001"]
    assert {o["offer_id"] for o in last.offers} == {"OFF-001"}  # cardiac check-up; no travel offers for a Riyadh doctor
    assert s.patient_id is not None and "file_number" not in s.reception  # identifiers dropped after the check


def test_wrong_date_of_birth_reveals_nothing_and_three_failures_continue_as_guest(db):
    s = ChatSession(facts=agent.PatientFacts(chief_complaint="chest_pain", pain_now=False))
    replies = []
    for _ in range(3):
        llm = FakeLLM(facts(file_number="MRN-100001", date_of_birth="1999-01-01"), text("could not verify"))
        replies.append(run_turn(db, llm, s, "MRN-100001, born 1 Jan 1999"))
    assert s.patient_id is None and s.is_guest and s.verify_attempts == 3
    assert all("Ahmed" not in x.reply for x in replies)  # never confirms that the file exists


def test_no_file_continues_as_guest(db):
    s = ChatSession(facts=agent.PatientFacts(chief_complaint="chest_pain", pain_now=False))
    r = run_turn(db, FakeLLM(facts(has_file=False), text("Any shortness of breath...?")), s, "no I don't have a file")
    assert s.is_guest and r.reply.startswith("No problem, we'll continue without a file.")


def test_patient_says_summary_is_wrong(db):
    s = _decided_session()
    s.stage, s.confirmed = "confirm", False
    r = run_turn(db, FakeLLM(facts(summary_confirmed=False)), s, "no, that's not right")
    assert r.reply == agent.CORRECTION_TEXT["en"] and r.providers == [] and not s.confirmed


def test_offers_never_on_emergency(db):
    r = run_turn(db, FakeLLM(), _decided_session(), "I can't breathe")
    assert r.offers == [] and r.providers == []


def test_emergency_from_facts_stops_before_any_provider_search(db):
    llm = FakeLLM(facts(chief_complaint="chest_pain", pain_now=True))  # nothing else scripted
    r = run_turn(db, llm, ChatSession(prefs={"city": "Riyadh"}), "chest pain, yes it hurts now")
    assert r.triage.care_path is CarePath.emergency and "997" in r.reply
    assert r.providers == [] and {h["hospital_id"] for h in r.hospitals} == {"HOSP-001", "HOSP-002"}  # ER hospitals, from DB
    assert llm.calls == 1


def test_red_flag_in_words_never_calls_the_llm(db):
    llm = FakeLLM()
    r = run_turn(db, llm, ChatSession(), "عندي ألم في صدري وما أقدر أتنفس")
    assert r.triage.care_path is CarePath.emergency and r.language == "ar" and "997" in r.reply
    assert llm.calls == 0


def test_once_emergency_the_session_stays_emergency(db):
    s = ChatSession()
    run_turn(db, FakeLLM(), s, "I can't breathe")
    r = run_turn(db, FakeLLM(), s, "ok but can you just find me a cardiologist?")
    assert r.triage.care_path is CarePath.emergency and r.providers == []


def _decided_session():
    """A guest who has passed reception and confirmed the clinic assistant's summary."""
    return ChatSession(facts=agent.PatientFacts(chief_complaint="chest_pain", pain_now=False, onset="days",
                                                has_diagnosis_to_review=False, **NO_SYMPTOMS),
                       stage="recommend", confirmed=True, is_guest=True)


def test_invented_doctor_is_never_shown(db):
    llm = FakeLLM(facts(), tool("search_providers", city="Riyadh"),
                  text("See [DOC-001], or my colleague [DOC-099] who is excellent."))
    r = run_turn(db, llm, _decided_session(), "ok")
    assert "DOC-099" not in r.reply and r.blocked == ["ungrounded_provider_reference"]
    assert all(p["doctor_id"] != "DOC-099" for p in r.providers)


def test_diagnosis_in_llm_reply_is_replaced(db):
    llm = FakeLLM(facts(), tool("search_providers"), text("It sounds like angina, see [DOC-001]."))
    r = run_turn(db, llm, _decided_session(), "ok")
    assert "angina" not in r.reply.lower() and r.blocked


def test_model_cannot_change_the_specialty_chosen_by_the_rules(db):
    llm = FakeLLM(facts(), tool("search_providers", specialty="orthopedics"), text("Options: [DOC-001]"))
    r = run_turn(db, llm, _decided_session(), "ok")
    assert r.providers and all(p["specialty"] == "Cardiology" for p in r.providers)


def test_no_results_is_said_plainly_even_if_the_model_claims_otherwise(db):
    llm = FakeLLM(facts(), tool("search_providers", city="Tokyo"), text("Here are three great cardiologists in Tokyo!"))
    r = run_turn(db, llm, _decided_session(), "I'm in Tokyo")
    assert r.providers == [] and "couldn't find a matching provider" in r.reply


def test_ai_down_gives_clear_message_not_a_crash(db):
    r = run_turn(db, FakeLLM(LLMUnavailable), ChatSession(), "I have chest pain")
    assert "temporarily unavailable" in r.reply and "997" in r.reply


def test_db_down_during_search_fabricates_nothing(db, monkeypatch):
    def boom(*a, **k):
        raise OperationalError("select", {}, Exception("db down"))
    monkeypatch.setattr(repository, "search_doctors", boom)
    llm = FakeLLM(facts(), tool("search_providers"), text("Here is [DOC-001]!"))
    r = run_turn(db, llm, _decided_session(), "ok")
    assert r.providers == [] and "unable to access the provider database" in r.reply


def test_gateway_authorisation(db):
    emergency = TriageResult(care_path=CarePath.emergency, rule_id="E_PAIN_NOW", reason="")
    gw = ToolGateway(session=db, triage=emergency, known_doctor_ids=set())
    assert json.loads(gw.call("search_providers", "{}"))["error"] == "not_allowed"

    spec = TriageResult(care_path=CarePath.specialist, rule_id="S", reason="", specialty="cardiology")
    gw = ToolGateway(session=db, triage=spec, known_doctor_ids=set())
    assert json.loads(gw.call("get_doctor_details", '{"doctor_id": "DOC-013"}'))["error"] == "not_allowed"
    assert json.loads(gw.call("get_hospital_details", '{"hospital_id": "HOSP-005"}'))["error"] == "not_allowed"
    assert json.loads(gw.call("search_providers", '{"country": "SAUDI"}'))["error"] == "invalid_arguments"


def test_chat_endpoint_and_rate_limit(client, monkeypatch):
    app.dependency_overrides[chat_api.get_llm] = lambda: FakeLLM(facts(chief_complaint="chest_pain"), text("Is it happening now?"))
    try:
        r = client.post("/api/v1/chat", json={"message": "I have chest pain"})
        body = r.json()
        assert r.status_code == 200 and body["triage"]["rule_id"] == "Q_PAIN_NOW" and len(body["session_id"]) == 32
        assert client.post("/api/v1/chat", json={"message": ""}).status_code == 422
        assert client.post("/api/v1/chat", json={"message": "x" * 1001}).status_code == 422

        monkeypatch.setattr(chat_api, "get_settings", lambda: type("S", (), {"rate_limit_per_minute": 0})())
        r = client.post("/api/v1/chat", json={"message": "I can't breathe"})
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    finally:
        app.dependency_overrides.clear()


def test_missed_answer_gets_one_focused_retry_instead_of_repeating_the_question(db):
    # Live-run regression (DeepSeek): "لا ما عندي تشخيص" was sometimes not extracted,
    # so the same question was asked again.
    s = _decided_session()
    s.facts.has_diagnosis_to_review = None
    s.last_ask = ["has_diagnosis_to_review"]
    s.history = [{"role": "assistant", "content": "هل لديك تشخيص سابق؟"}]
    llm = FakeLLM(facts(),                                   # full extraction misses the answer
                  facts(has_diagnosis_to_review=False),       # focused retry catches it
                  tool("search_providers", city="Riyadh"), text("[DOC-001]"))
    r = run_turn(db, llm, s, "لا ما عندي تشخيص")
    assert r.triage.rule_id == "S_CARDIOLOGY"
    assert any(t.get("step") == "extract_focused_retry" for t in r.trace)


def test_english_file_summary_uses_english_punctuation(db):
    from app import patients
    from app.models import Patient
    p = db.query(Patient).filter_by(file_number="MRN-100001").one()
    assert "Hypertension, Type 2 diabetes" in patients.file_summary(db, p, "en")
    assert "،" in patients.file_summary(db, p, "ar")


@pytest.mark.parametrize("invented", [
    "A great option is Dr. Ahmed Al-Something at King Faisal Hospital.",
    "You could also see Dr Khalid Mansour in Jeddah.",
    "أنصحك بالدكتور أحمد السالم في مستشفى الملك فيصل",
    "Try the Mayo Clinic for a second opinion.",
])
def test_invented_provider_names_without_ids_are_never_shown(db, invented):
    # Gap found on review: grounding only checked IDs (DOC-001), so a made-up NAME with no ID passed.
    llm = FakeLLM(facts(), tool("search_providers", city="Riyadh"), text("[DOC-001]. " + invented))
    r = run_turn(db, llm, _decided_session(), "ok")
    assert "Al-Something" not in r.reply and "Khalid Mansour" not in r.reply
    assert "السالم" not in r.reply and "Mayo" not in r.reply
    assert r.blocked


def test_question_turns_cannot_mention_providers_either(db):
    # The question phase has no tool results at all, so ANY provider mention is invented.
    s = ChatSession(facts=agent.PatientFacts(chief_complaint="chest_pain"))
    r = run_turn(db, FakeLLM(facts(), text("Dr. Sami at Al Noor Hospital can help. Is the pain happening now?")), s, "chest pain")
    assert "Sami" not in r.reply and "Noor" not in r.reply and "happening right now" in r.reply


def test_patient_asking_for_a_doctor_not_in_the_database_gets_an_honest_answer(db):
    # Live DeepSeek finding: the model's "Dr. X is not in our network" repeated the invented name,
    # was (correctly) blocked, and the fallback ignored the patient's question.
    llm = FakeLLM(facts(), tool("search_providers", city="Riyadh"),
                  text("Dr. Ahmed Al-Zahrani is not in our network, but [DOC-001] is."))
    r = run_turn(db, llm, _decided_session(), "Can I book with Dr. Ahmed Al-Zahrani at King Faisal Specialist Hospital?")
    assert r.reply.startswith("I can only book doctors in the HealTrip network")
    assert "Al-Zahrani" not in r.reply and [p["doctor_id"] for p in r.providers] == ["DOC-001"]


def test_identity_numbers_never_reach_the_llm_or_the_stored_history(db):
    # Audit finding: "MRN-100001" / the DOB answer stayed in history (sent to the LLM, kept 30 min).
    s = ChatSession(facts=agent.PatientFacts(chief_complaint="chest_pain", pain_now=False))
    seen = []
    class Spy(FakeLLM):
        def chat(self, messages, tools=None, tool_choice=None):
            seen.append(str(messages))
            return super().chat(messages, tools, tool_choice)
    run_turn(db, Spy(facts(has_file=True), text("DOB?")), s, "yes my file is MRN-100001")
    run_turn(db, Spy(facts(date_of_birth="1971-04-12"), text("Any shortness of breath?")), s, "12/04/1971")
    assert s.patient_id is not None                                # still verified correctly
    assert not any("MRN-100001" in m for m in seen)                # the LLM never saw the file number
    assert "MRN-100001" not in str(s.history) and "12/04/1971" not in str(s.history)


def test_hospital_inbox_requires_a_ticket(client):
    assert client.get("/api/v1/hospital/inbox").status_code == 422
