from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Booking, Notification

router = APIRouter(prefix="/api/v1/hospital", tags=["hospital (mock)"])


@router.get("/inbox")
def inbox(db: Annotated[Session, Depends(get_session)],
          ticket: Annotated[str, Query(pattern=r"^HT-\d{4}-\d{6}$")]):
    """DEMO ONLY: what the doctor's email and the hospital system received for ONE ticket (you must
    know the ticket number). Audit finding: it used to list every notification publicly. In
    production each hospital sees only its own data, behind authentication."""
    stmt = select(Notification, Booking).join(Booking, Notification.booking_id == Booking.id)
    stmt = stmt.where(Booking.ticket_number == ticket)
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(50)).all()
    return [{"channel": n.channel, "recipient": n.recipient, "subject": n.subject, "body": n.body,
             "status": n.status, "ticket_number": b.ticket_number, "sent_at": n.sent_at} for n, b in rows]
