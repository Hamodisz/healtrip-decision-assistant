from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Booking, Notification

router = APIRouter(prefix="/api/v1/hospital", tags=["hospital (mock)"])


@router.get("/inbox")
def inbox(db: Annotated[Session, Depends(get_session)],
          doctor_id: Annotated[str | None, Query(pattern=r"^DOC-\d{3}$")] = None):
    """DEMO ONLY: what the doctor's email and the hospital system received. In production each
    hospital sees only its own data, behind authentication."""
    stmt = select(Notification, Booking).join(Booking, Notification.booking_id == Booking.id)
    if doctor_id:
        stmt = stmt.where(Booking.doctor_id == doctor_id)
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(50)).all()
    return [{"channel": n.channel, "recipient": n.recipient, "subject": n.subject, "body": n.body,
             "status": n.status, "ticket_number": b.ticket_number, "sent_at": n.sent_at} for n, b in rows]
