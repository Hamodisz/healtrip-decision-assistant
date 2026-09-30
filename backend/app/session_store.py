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
