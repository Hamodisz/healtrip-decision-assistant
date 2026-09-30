import time
from collections import defaultdict, deque
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import ChatSession, run_turn
from app.config import get_settings
from app.db import get_session
from app.llm import LLM, OpenAICompatLLM
from app.triage import TriageResult

router = APIRouter(prefix="/api/v1", tags=["chat"])

SESSION_TTL_S = 30 * 60
MAX_SESSIONS = 1000
_sessions: dict[str, tuple[float, ChatSession]] = {}   # in memory only, expires after 30 min
_hits: dict[str, deque] = defaultdict(deque)


def get_llm() -> LLM:
    return OpenAICompatLLM()


def rate_limit(request: Request) -> None:
    """Simple sliding window per client IP. Production: per-user limits in a shared store (Redis)."""
    ip = request.client.host if request.client else "unknown"
    now, q = time.monotonic(), _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= get_settings().rate_limit_per_minute:
        raise HTTPException(status_code=429, detail="Too many messages. Please wait a minute.")
    q.append(now)


def _get_or_create(session_id: str | None) -> ChatSession:
    now = time.monotonic()
    for sid in [k for k, (t, _) in _sessions.items() if now - t > SESSION_TTL_S]:
        del _sessions[sid]
    if session_id and session_id in _sessions:
        s = _sessions[session_id][1]
    else:
        if len(_sessions) >= MAX_SESSIONS:
            raise HTTPException(status_code=503, detail="Service busy. Please try again shortly.")
        s = ChatSession()
    _sessions[s.id] = (now, s)
    return s


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    message: str = Field(min_length=1, max_length=1000)
    language: Literal["en", "ar"] | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    language: str
    triage: TriageResult | None
    providers: list[dict]        # DB rows only: the UI renders these, never the model's text
    hospitals: list[dict]
    trace: list[dict]            # what happened this turn (facts → rule → tools); no patient text
    disclaimer: str = "Prototype with mock data. Not medical advice; this assistant does not diagnose."


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(rate_limit)])
def chat(req: ChatRequest, db: Annotated[Session, Depends(get_session)], llm: Annotated[LLM, Depends(get_llm)]):
    s = _get_or_create(req.session_id)
    r = run_turn(db, llm, s, req.message.strip(), req.language)
    return ChatResponse(session_id=s.id, reply=r.reply, language=r.language, triage=r.triage,
                        providers=r.providers, hospitals=r.hospitals, trace=r.trace)
