"""Doctor / hospital notification, MOCKED for the demo.

Real design: a background worker reads pending outbox rows and calls an email provider and the
hospital's system (e.g. a FHIR `Appointment` resource or a webhook into the HIS), retrying until
delivered. Here, "sending" marks the row as sent and logs it, and GET /api/v1/hospital/inbox
shows what the doctor and hospital would have received. Nothing leaves the machine.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Booking, Notification, Patient

log = logging.getLogger("healtrip.notify")
MAX_ATTEMPTS = 5


def enqueue_for_booking(db: Session, booking: Booking) -> None:
    """Call INSIDE the booking transaction (outbox pattern)."""
    d, slot = booking.doctor, booking.slot
    local = slot.starts_at.astimezone(ZoneInfo(d.hospital.timezone)).strftime("%a %d %b %Y, %H:%M")
    mode = "remote consultation" if slot.mode == "remote" else "in-person visit"
    p = db.get(Patient, booking.patient_id) if booking.patient_id else None
    who = f"Patient: {p.name_en} (file {p.file_number})" if p else "Patient: new patient, no file yet"
    db.add_all([
        Notification(
            booking_id=booking.id, channel="doctor_email",
            recipient=f"{d.id.lower()}@{d.hospital.id.lower()}.demo-hospital.example",  # reserved test domain
            subject=f"New appointment {booking.ticket_number}: {local}",
            body=(f"Dear {d.name_en},\n\nA new {mode} has been booked through HealTrip.\n"
                  f"Ticket: {booking.ticket_number}\nTime: {local} ({d.hospital.timezone})\n"
                  f"Location: {d.hospital.name_en}, {d.hospital.address}\n{who}\n\n"
                  "The patient will present this ticket on arrival. (Demo notification, no real email sent.)"),
        ),
        Notification(
            booking_id=booking.id, channel="hospital_system",
            recipient=f"HIS:{d.hospital.id}",
            subject=f"Appointment.create {booking.ticket_number}",
            body=(f'{{"resourceType":"Appointment","status":"booked","identifier":"{booking.ticket_number}",'
                  f'"start":"{slot.starts_at.isoformat()}","minutesDuration":{slot.duration_min},'
                  f'"participant":["Practitioner/{d.id}","Location/{d.hospital.id}"'
                  + (f',"Patient/{p.file_number}"' if p else "") + "]}"),
        ),
    ])


def deliver_pending(db: Session) -> int:
    """Mock sender. In production this is a worker with retries; here it runs right after the booking commits."""
    sent = 0
    with db.begin():
        rows = db.scalars(select(Notification).where(Notification.status == "pending").with_for_update(skip_locked=True))
        for n in rows:
            n.attempts += 1
            n.status, n.sent_at = "sent", datetime.now(timezone.utc)
            log.info("notification_sent channel=%s recipient=%s subject=%s", n.channel, n.recipient, n.subject)
            sent += 1
    return sent
