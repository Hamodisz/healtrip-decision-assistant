import hmac

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import session_store
from app.db import get_session

from app.config import get_settings
from app.seed import reset_schema, seed, top_up_slots

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], include_in_schema=False)


@router.post("/seed")
def seed_database(x_seed_token: str = Header(default="")):
    """One-time seeding of a hosted database from inside the hosting network (the DB port isn't
    reachable from every developer machine). Disabled unless SEED_TOKEN is set; remove the env
    var after use and the endpoint answers 404 again."""
    token = get_settings().seed_token
    if not token or not hmac.compare_digest(x_seed_token, token):
        raise HTTPException(status_code=404, detail="Not found")
    reset_schema()
    return seed()


@router.get("/cron")
def daily_maintenance(db: Annotated[Session, Depends(get_session)], authorization: str = Header(default="")):
    """Run daily by Vercel Cron (vercel.json): keeps 14 days of open slots so the demo never
    'runs out' of appointments, and deletes expired conversations and old rate-limit rows."""
    secret = get_settings().cron_secret
    if not secret or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(status_code=404, detail="Not found")
    from app.db import Base, engine
    Base.metadata.create_all(engine)  # creates tables added since the last deploy; never drops or alters
    added = top_up_slots(db)
    return {"slots_added": added, **session_store.purge_expired(db)}
