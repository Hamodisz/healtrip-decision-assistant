from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.triage import Assessment, PatientFacts, assess

router = APIRouter(prefix="/api/v1", tags=["triage"])


class TriageRequest(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    facts: PatientFacts = Field(default_factory=PatientFacts)
    language: Literal["en", "ar"] = "en"


@router.post("/triage", response_model=Assessment)
def triage(req: TriageRequest):
    """Deterministic: no LLM call. Useful for testing the rules directly, and it is the same
    function the chat agent calls on every turn (M3)."""
    return assess(req.message, req.facts, req.language)
