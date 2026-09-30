"""Booking: the AI proposes real times, the PATIENT confirms, the DATABASE issues the ticket."""
import re
import threading

from app import repository as repo
from app.agent import ChatSession, PatientFacts
from app.triage import CarePath
from app import session_store
from app.api import chat as chat_api
from app.db import SessionLocal
from app.main import app
from tests.test_agent import NO_SYMPTOMS, FakeLLM, facts, text, tool


def _decided_session_in_store() -> ChatSession:
    s = ChatSession(facts=PatientFacts(chief_complaint="chest_pain", pain_now=False, onset="days",
                                       has_diagnosis_to_review=False, **NO_SYMPTOMS),
                    stage="recommend", confirmed=True, is_guest=True)
    with SessionLocal() as db:
        session_store.save(db, s)
    return s


def _chat(client, s, msg, llm):
    app.dependency_overrides[chat_api.get_llm] = lambda: llm
    try:
        with SessionLocal() as db:
            session_store.save(db, s)
        body = client.post("/api/v1/chat", json={"session_id": s.id, "message": msg}).json()
        with SessionLocal() as db:  # pick up what the endpoint saved
            s.__dict__.update(session_store.load(db, s.id).__dict__)
        return body
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
        session_store.save(db, s)
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
    with SessionLocal() as db:
        session_store.save(db, s)
    assert client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": 1}).status_code == 409


def test_model_cannot_announce_a_booking_or_invent_a_ticket(client):
    s = _decided_session_in_store()
    _chat(client, s, "Riyadh", FakeLLM(facts(city="Riyadh"), tool("search_providers", city="Riyadh"), text("[DOC-001]")))
    body = _chat(client, s, "book the first time", FakeLLM(
        facts(), tool("get_available_slots", doctor_id="DOC-001"), text("Done! You're booked, ticket HT-2026-999999.")))
    assert "HT-2026-999999" not in body["reply"] and "Confirm" in body["reply"]


def test_booking_notifies_doctor_and_hospital_without_symptoms(client):
    s = _decided_session_in_store()
    _chat(client, s, "Riyadh", FakeLLM(facts(city="Riyadh"), tool("search_providers", city="Riyadh"), text("[DOC-001]")))
    body = _chat(client, s, "times?", FakeLLM(facts(), tool("get_available_slots", doctor_id="DOC-001"), text("Tap Confirm.")))
    b = client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": body["slots"][1]["slot_id"]}).json()

    inbox = [n for n in client.get("/api/v1/hospital/inbox", params={"doctor_id": "DOC-001"}).json()
             if n["ticket_number"] == b["ticket_number"]]
    assert {n["channel"] for n in inbox} == {"doctor_email", "hospital_system"}
    assert all(n["status"] == "sent" for n in inbox)
    assert all("chest" not in n["body"].lower() for n in inbox)  # no symptoms leave the conversation
    assert "notified" in b["reply"]


def test_rolled_back_booking_notifies_no_one():
    from sqlalchemy import func, select

    from app import notifications
    from app.models import Notification
    with SessionLocal() as db:
        before = db.scalar(select(func.count(Notification.id)))
        slot_id = repo.available_slots(db, "DOC-007", limit=1)[0].id
    try:
        with SessionLocal() as db, db.begin():
            b = repo.book_slot(db, slot_id)
            notifications.enqueue_for_booking(db, b)
            raise RuntimeError("crash after booking, before commit")
    except RuntimeError:
        pass
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Notification.id))) == before
        assert slot_id in {x.id for x in repo.available_slots(db, "DOC-007", limit=20)}


def test_verified_patient_booking_links_the_file_for_the_hospital(client):
    from app.models import Patient
    s = _decided_session_in_store()
    with SessionLocal() as db:
        s.patient_id = db.query(Patient).filter_by(file_number="MRN-100003").one().id
        session_store.save(db, s)
    _chat(client, s, "Riyadh", FakeLLM(facts(city="Riyadh"), tool("search_providers", city="Riyadh"), text("[DOC-001]")))
    body = _chat(client, s, "times?", FakeLLM(facts(), tool("get_available_slots", doctor_id="DOC-001"), text("Tap Confirm.")))
    b = client.post("/api/v1/bookings", json={"session_id": s.id, "slot_id": body["slots"][3]["slot_id"]}).json()
    inbox = [n for n in client.get("/api/v1/hospital/inbox").json() if n["ticket_number"] == b["ticket_number"]]
    his = next(n for n in inbox if n["channel"] == "hospital_system")
    assert "Patient/MRN-100003" in his["body"]
    assert "Mitral" not in str(inbox)  # the file is linked, the medical history is not sent


def test_session_survives_a_round_trip_through_the_database():
    # Serverless: the next message may hit another instance, so state must round-trip exactly.
    from datetime import date
    s = _decided_session_in_store()
    s.reception["date_of_birth"] = date(1971, 4, 12)
    s.known_doctor_ids, s.offered_slot_ids = {"DOC-001"}, {5, 7}
    with SessionLocal() as db:
        session_store.save(db, s)
        loaded = session_store.load(db, s.id)
    assert loaded.facts == s.facts and loaded.offered_slot_ids == {5, 7} and loaded.confirmed
    assert loaded.reception["date_of_birth"] == date(1971, 4, 12)


def test_daily_cap_stops_the_public_demo(client, monkeypatch):
    monkeypatch.setattr(chat_api, "get_settings", lambda: type("S", (), {"daily_turn_cap": 0, "rate_limit_per_minute": 99})())
    r = client.post("/api/v1/chat", json={"message": "hello"})
    assert r.status_code == 503 and "daily usage limit" in r.json()["error"]["message"]
