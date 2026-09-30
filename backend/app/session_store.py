"""Conversation state in Postgres (not process memory), because on serverless hosting each
request can land on a different instance. Sessions expire after 30 minutes of inactivity."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.agent import ChatSession, session_from_dict, session_to_dict
from app.models import ChatSessionRow

SESSION_TTL = timedelta(minutes=30)


def load(db: Session, session_id: str | None) -> ChatSession | None:
    if not session_id:
        return None
    row = db.get(ChatSessionRow, session_id)
    result = None if not row or row.updated_at < datetime.now(timezone.utc) - SESSION_TTL else session_from_dict(row.data)
    db.commit()  # end the read transaction so callers can open their own
    return result


def save(db: Session, s: ChatSession) -> None:
    db.merge(ChatSessionRow(id=s.id, data=session_to_dict(s), updated_at=datetime.now(timezone.utc)))
    db.execute(delete(ChatSessionRow).where(ChatSessionRow.updated_at < datetime.now(timezone.utc) - SESSION_TTL))
    db.commit()


def count_turn(db: Session) -> int:
    """Atomically count today's chat turns; returns the new total."""
    total = db.execute(text(
            "INSERT INTO daily_usage (day, chat_turns) VALUES (:d, 1) "
            "ON CONFLICT (day) DO UPDATE SET chat_turns = daily_usage.chat_turns + 1 RETURNING chat_turns"),
            {"d": date.today()}).scalar_one()
    db.commit()
    return total


def hit_rate_limit(db: Session, key: str, limit: int) -> bool:
    """True if `key` exceeded `limit` requests in the current minute (shared across instances)."""
    now = datetime.now(timezone.utc)
    window = now.replace(second=0, microsecond=0)
    hits = db.execute(text(
        "INSERT INTO rate_hits (key, window_start, hits) VALUES (:k, :w, 1) "
        "ON CONFLICT (key, window_start) DO UPDATE SET hits = rate_hits.hits + 1 RETURNING hits"),
        {"k": key[:80], "w": window}).scalar_one()
    db.commit()
    return hits > limit


def purge_expired(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    sessions = db.execute(delete(ChatSessionRow).where(ChatSessionRow.updated_at < now - SESSION_TTL)).rowcount
    rates = db.execute(text("DELETE FROM rate_hits WHERE window_start < :c"), {"c": now - timedelta(hours=1)}).rowcount
    db.commit()
    return {"expired_sessions_deleted": sessions, "old_rate_rows_deleted": rates}
