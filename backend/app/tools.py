"""Tools the agent can call, and the gateway that decides whether it may.

The model proposes a tool call; code validates the arguments (Pydantic), checks the call is
allowed for the current care path, and may OVERRIDE arguments that clinical rules own
(specialty, second_opinion). Results are DB rows, and they're recorded so the reply can be
checked against them (grounding).
"""
import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import repository as repo
from app.triage import CarePath, TriageResult

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_providers",
        "description": "Search HealTrip's provider database for doctors. The specialty is set by the "
                       "clinical rules; you choose location/language filters from what the patient said.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "City in English, e.g. Riyadh"},
            "country": {"type": "string", "description": "ISO-2 code, e.g. SA"},
            "language": {"type": "string", "description": "ISO-639-1 code the patient prefers, e.g. ar"},
            "remote": {"type": "boolean", "description": "Only doctors offering remote consultation"},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_doctor_details",
        "description": "Full details for a doctor previously returned by search_providers.",
        "parameters": {"type": "object", "properties": {"doctor_id": {"type": "string"}},
                       "required": ["doctor_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_hospital_details",
        "description": "Full details for a hospital of a doctor previously returned.",
        "parameters": {"type": "object", "properties": {"hospital_id": {"type": "string"}},
                       "required": ["hospital_id"]},
    }},
]

SEARCH_PATHS = {CarePath.specialist, CarePath.second_opinion, CarePath.routine}


class SearchArgs(BaseModel):
    city: str | None = Field(default=None, min_length=2, max_length=60)
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    remote: bool | None = None


class DoctorArgs(BaseModel):
    doctor_id: str = Field(pattern=r"^DOC-\d{3}$")


class HospitalArgs(BaseModel):
    hospital_id: str = Field(pattern=r"^HOSP-\d{3}$")


def doctor_card(d) -> dict:
    """The only shape of provider data the model and the UI ever see: built from a DB row."""
    return {
        "doctor_id": d.id, "doctor_name": d.name_en, "doctor_name_ar": d.name_ar,
        "specialty": d.specialty.name_en, "specialty_ar": d.specialty.name_ar,
        "hospital_id": d.hospital.id, "hospital_name": d.hospital.name_en, "hospital_name_ar": d.hospital.name_ar,
        "city": d.hospital.city, "country": d.hospital.country, "languages": d.languages,
        "years_experience": d.years_experience, "offers_second_opinion": d.offers_second_opinion,
        "offers_remote_consult": d.offers_remote_consult,
        "consultation_fee": float(d.consultation_fee), "currency": d.currency, "is_mock": d.is_mock,
    }


def hospital_card(h) -> dict:
    return {"hospital_id": h.id, "hospital_name": h.name_en, "hospital_name_ar": h.name_ar,
            "city": h.city, "country": h.country, "address": h.address,
            "has_emergency": h.has_emergency, "languages": h.languages, "is_mock": h.is_mock}


@dataclass
class ToolGateway:
    session: Session
    triage: TriageResult
    known_doctor_ids: set[str]                       # returned earlier in this conversation
    doctors: dict[str, dict] = field(default_factory=dict)   # returned THIS turn (for grounding + UI)
    hospitals: dict[str, dict] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    db_failed: bool = False
    searched_empty: bool = False

    def call(self, name: str, raw_args: str) -> str:
        """Execute one tool call. Always returns a JSON string for the model, never raises."""
        try:
            args = json.loads(raw_args or "{}")
            result = self._dispatch(name, args)
        except ValidationError as e:
            result = {"error": "invalid_arguments", "fields": [".".join(map(str, x["loc"])) for x in e.errors()]}
        except json.JSONDecodeError:
            result = {"error": "invalid_arguments"}
        except SQLAlchemyError:
            self.db_failed = True
            result = {"error": "provider_database_unavailable"}
        self.trace.append({"tool": name, "args": raw_args, "result_summary": _summary(result)})
        return json.dumps(result, ensure_ascii=False)

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "search_providers":
            if self.triage.care_path not in SEARCH_PATHS:
                return {"error": "not_allowed", "reason": f"provider search is disabled on {self.triage.care_path.value} path"}
            a = SearchArgs(**args)
            # Clinical rules own specialty and second_opinion: the model cannot change them.
            docs = repo.search_doctors(self.session, specialty=self.triage.specialty, city=a.city,
                                       country=a.country, language=a.language, remote_consult=a.remote,
                                       second_opinion=True if self.triage.second_opinion else None, limit=5)
            cards = [doctor_card(d) for d in docs]
            self.doctors.update({c["doctor_id"]: c for c in cards})
            if not cards:
                self.searched_empty = True
                return {"results": [], "note": "no_matching_providers_in_database"}
            return {"results": cards}

        if name == "get_doctor_details":
            a = DoctorArgs(**args)
            if a.doctor_id not in self.known_doctor_ids | set(self.doctors):
                return {"error": "not_allowed", "reason": "doctor_id was not returned by a search in this conversation"}
            d = repo.get_doctor(self.session, a.doctor_id)
            if not d:
                return {"error": "not_found"}
            card = doctor_card(d)
            self.doctors[d.id] = card
            return card

        if name == "get_hospital_details":
            a = HospitalArgs(**args)
            allowed = {c["hospital_id"] for c in self.doctors.values()}
            if a.hospital_id not in allowed:
                return {"error": "not_allowed", "reason": "hospital is not linked to a doctor returned in this conversation"}
            h = repo.get_hospital(self.session, a.hospital_id)
            if not h:
                return {"error": "not_found"}
            card = hospital_card(h)
            self.hospitals[h.id] = card
            return card

        return {"error": "unknown_tool"}


def _summary(result: dict) -> str:
    if "results" in result:
        return f"{len(result['results'])} result(s): " + ", ".join(r["doctor_id"] for r in result["results"])
    if "error" in result:
        return "error: " + result["error"]
    return "ok: " + str(result.get("doctor_id") or result.get("hospital_id"))
