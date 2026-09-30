import hmac

from fastapi import APIRouter, Header, HTTPException

from app.config import get_settings
from app.seed import reset_schema, seed

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
