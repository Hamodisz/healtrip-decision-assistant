"""Booking: the AI proposes real times, the PATIENT confirms, the DATABASE issues the ticket."""
import re
import threading

from app import repository as repo
from app.agent import ChatSession, PatientFacts
from app.triage import CarePath
from app.api import chat as chat_api
from app.db import SessionLocal
from app.main import app
from tests.test_agent import NO_SYMPTOMS, FakeLLM, facts, text, tool


def _decided_session_in_store() -> ChatSession:
    s = ChatSession(facts=PatientFacts(chief_complaint="chest_pain", pain_now=False, onset="days",
                                       has_diagnosis_to_review=False, **NO_SYMPTOMS))
    chat_api._sessions[s.id] = (0.0, s)
    return s


def _chat(client, s, msg, llm):
    app.dependency_overrides[chat_api.get_llm] = lambda: llm
    try:
        chat_api._sessions[s.id] = (__import__("time").monotonic(), s)
        return client.post("/api/v1/chat", json={"session_id": s.id, "message": msg}).json()
    finally:
        app.dependency_overrides.clear()


def test_full_flow_search_then_times_then_confirm_then_db_ticket(client):
    s = _decided_session_in_store()
    body = _chat(client, s, "cardiologist in Riyadh please", FakeLLM(
        facts(city="Riyadh"), tool("search_providers", city="Riyadh"), text("A cardiologist fits: [DOC-001].")))
    assert [p["doctor_id"] for p in body["providers"]] == ["DOC-001"]

    body = _chat(client, s, "I want DOC-001, what times?", FakeLLM(
        facts(), tool("get_available_slots", doctor_id="DOC-001"), text("Tap Confirm on the time you want.")))
    assert len(body["slots"]) == 5 and all(x["doctor_id"] == "DOC-001" for x in body["slots"])
    chosen = body["slots"][0]

    r = client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": chosen["slot_id"]})
    b = r.json()
    assert r.status_code == 200
    assert re.fullmatch(r"HT-\d{4}-\d{6}", b["ticket_number"])
    assert b["ticket_number"] in b["reply"] and b["doctor"]["doctor_id"] == "DOC-001"

    with SessionLocal() as db:  # the time is gone for everyone else
        assert chosen["slot_id"] not in {x.id for x in repo.available_slots(db, "DOC-001", days=14, limit=20)}
    again = client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": chosen["slot_id"]})
    assert again.status_code == 409


def test_two_patients_same_slot_same_moment_only_one_wins():
    with SessionLocal() as db:
        slot_id = repo.available_slots(db, "DOC-003", limit=1)[0].id
    results, barrier = [], threading.Barrier(2)

    def attempt():
        with SessionLocal() as db:
            barrier.wait()
            try:
                with db.begin():
                    results.append(repo.book_slot(db, slot_id).ticket_number)
            except repo.SlotUnavailable:
                results.append("taken")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results)[-1] == "taken" and sum(r.startswith("HT-") for r in results) == 1


def test_cannot_book_a_time_that_was_not_offered(client):
    s = _decided_session_in_store()
    s.care_path = CarePath.specialist  # bookable path, so the ONLY reason to refuse is the slot
    with SessionLocal() as db:
        other = repo.available_slots(db, "DOC-014", limit=1)[0].id  # a real slot, but never offered here
    r = client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": other})
    assert r.status_code == 409 and "not offered" in r.json()["error"]["message"]


def test_cannot_get_times_for_a_doctor_the_system_did_not_return(client):
    s = _decided_session_in_store()
    body = _chat(client, s, "book DOC-099", FakeLLM(
        facts(), tool("get_available_slots", doctor_id="DOC-099"), text("ok")))
    assert body["slots"] == [] and "DOC-099" not in str(body["providers"])
    assert "not_allowed" in str(body["trace"])


def test_emergency_conversation_cannot_book(client):
    s = ChatSession(emergency_locked=True, offered_slot_ids={1})
    chat_api._sessions[s.id] = (0.0, s)
    assert client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": 1}).status_code == 409


def test_model_cannot_announce_a_booking_or_invent_a_ticket(client):
    s = _decided_session_in_store()
    _chat(client, s, "Riyadh", FakeLLM(facts(city="Riyadh"), tool("search_providers", city="Riyadh"), text("[DOC-001]")))
    body = _chat(client, s, "book the first time", FakeLLM(
        facts(), tool("get_available_slots", doctor_id="DOC-001"), text("Done! You're booked, ticket HT-2026-999999.")))
    assert "HT-2026-999999" not in body["reply"] and "Confirm" in body["reply"]
