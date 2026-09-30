"""Cross-sell / upsell selection: code, not the LLM, and strictly AFTER the clinical decision.

Guardrails: never called on emergency/urgent paths; the triage rules never see offers or prices;
offers come only from the `offers` table; travel offers only when a matched doctor is abroad.
"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Offer
from app.triage import CarePath, TriageResult

OFFER_PATHS = {CarePath.specialist, CarePath.second_opinion, CarePath.routine}


def select_offers(db: Session, triage: TriageResult, doctor_countries: set[str], home_country: str = "SA") -> list[dict]:
    if triage.care_path not in OFFER_PATHS:
        return []
    stmt = select(Offer).where(Offer.applicable_paths.any(triage.care_path.value),
                               or_(Offer.specialty_code.is_(None), Offer.specialty_code == triage.specialty))
    abroad = any(c != home_country for c in doctor_countries)
    return [{"offer_id": o.id, "kind": o.kind, "title_en": o.title_en, "title_ar": o.title_ar,
             "description_en": o.description_en, "description_ar": o.description_ar,
             "price": float(o.price), "currency": o.currency, "is_mock": o.is_mock}
            for o in db.scalars(stmt.order_by(Offer.id)) if abroad or not o.requires_travel][:4]
