from datetime import datetime, timedelta, timezone

from app import repository as repo
from app.db import SessionLocal


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_filter_by_specialty_and_second_opinion(client):
    docs = client.get("/api/v1/doctors", params={"specialty": "cardiology", "second_opinion": True}).json()
    assert {d["id"] for d in docs} == {"DOC-001", "DOC-003", "DOC-004"}
    assert all(d["offers_second_opinion"] for d in docs)


def test_filter_by_language_and_country(client):
    docs = client.get("/api/v1/doctors", params={"country": "tr", "language": "ar"}).json()
    assert docs and all("ar" in d["languages"] and d["hospital"]["country"] == "TR" for d in docs)


def test_every_row_is_marked_mock(client):
    assert all(d["is_mock"] for d in client.get("/api/v1/doctors").json())
    assert all(h["is_mock"] and h["name_en"].startswith("Demo") for h in client.get("/api/v1/hospitals").json())


def test_er_hospitals(client):
    hs = client.get("/api/v1/hospitals", params={"city": "Riyadh", "has_emergency": True}).json()
    assert {h["id"] for h in hs} == {"HOSP-001", "HOSP-002"}


def test_unknown_doctor_is_clean_404(client):
    r = client.get("/api/v1/doctors/DOC-999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_malformed_input_rejected_before_query(client):
    for params in ({"specialty": "cardiology' OR 1=1--"}, {"country": "SAU"}, {"language": "arabic"}):
        r = client.get("/api/v1/doctors", params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "invalid_input"
    assert client.get("/api/v1/doctors/1;DROP TABLE doctors").status_code in (404, 422)


def test_emergency_doctors_have_no_appointments(client):
    assert client.get("/api/v1/doctors/DOC-005/slots").json() == []


def test_slots_are_future_and_in_local_working_hours(client):
    slots = client.get("/api/v1/doctors/DOC-001/slots", params={"days": 14}).json()
    assert slots
    for s in slots:
        start = datetime.fromisoformat(s["starts_at"])
        assert start > datetime.now(timezone.utc)
        local = start.astimezone(timezone(timedelta(hours=3)))  # Riyadh
        assert local.weekday() not in (4, 5)  # no Friday/Saturday in KSA
        assert 9 <= local.hour <= 17


def test_expired_hold_becomes_available_again_but_active_hold_does_not():
    with SessionLocal.begin() as s:
        a, b = repo.available_slots(s, "DOC-002", limit=2)
        a.status, a.held_until = "held", datetime.now(timezone.utc) + timedelta(minutes=10)
        b.status, b.held_until = "held", datetime.now(timezone.utc) - timedelta(minutes=1)
        a_id, b_id = a.id, b.id
    with SessionLocal() as s:
        ids = {x.id for x in repo.available_slots(s, "DOC-002", limit=20)}
    assert a_id not in ids  # active hold: hidden
    assert b_id in ids  # expired hold: offered again


def test_result_cap():
    with SessionLocal() as s:
        assert len(repo.search_doctors(s, limit=500)) <= repo.MAX_RESULTS


def test_hospital_details(client):
    h = client.get("/api/v1/hospitals/HOSP-005").json()
    assert h["city"] == "Istanbul" and h["address"] and h["is_mock"]
    assert client.get("/api/v1/hospitals/HOSP-999").status_code == 404
