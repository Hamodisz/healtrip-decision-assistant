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


def test_full_chest_pain_conversation_asks_then_recommends_real_doctors(db):
    s = ChatSession()
    llm = FakeLLM(
        facts(chief_complaint="chest_pain"), text("I understand. Is the pain happening right now?"),
        facts(pain_now=False), text("Thanks. Any shortness of breath, fainting, sweating or spreading pain?"),
        facts(**NO_SYMPTOMS), text("When did it start?"),
        facts(onset="weeks_or_more", city="Riyadh", language="ar"), text("Do you have a diagnosis to review?"),
        facts(has_diagnosis_to_review=False),
        tool("search_providers", city="Riyadh", language="ar"),
        text("A cardiologist is the right next step. [DOC-001] is available in Riyadh."),
    )
    turns = ["I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion.",
             "no", "none of those", "a few weeks ago, I'm in Riyadh and prefer Arabic", "no"]
    results = [run_turn(db, llm, s, m) for m in turns]

    assert [r.triage.rule_id for r in results] == ["Q_PAIN_NOW", "Q_ASSOCIATED", "Q_ONSET", "Q_REVIEW", "S_CARDIOLOGY"]
    last = results[-1]
    assert last.triage.care_path is CarePath.specialist
    assert {p["doctor_id"] for p in last.providers} == {"DOC-001"}  # the only Riyadh cardiologist speaking ar
    assert "[DOC-001]" in last.reply and last.blocked == []
    assert any(t.get("tool") == "search_providers" for t in last.trace)


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
    return ChatSession(facts=agent.PatientFacts(chief_complaint="chest_pain", pain_now=False, onset="days",
                                                has_diagnosis_to_review=False, **NO_SYMPTOMS))


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
