import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import admin, chat, hospital, providers, triage
from app.config import get_settings
from app.db import engine

log = logging.getLogger("healtrip")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="HealTrip Decision Assistant API",
    version="0.1.0",
    description="Decision-First patient assistant: prototype with mock data only.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,  # explicit allow-list, never "*"
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ── Error handling: one consistent error shape, no stack traces or SQL ever leak to clients ──
def _error(status: int, code: str, message: str, request_id: str | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    code = {404: "not_found", 409: "conflict", 429: "rate_limited", 503: "unavailable"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
    return _error(422, "invalid_input", f"Invalid value for: {fields}")


@app.exception_handler(SQLAlchemyError)
async def db_error(_: Request, exc: SQLAlchemyError):
    request_id = uuid.uuid4().hex[:12]
    log.exception("db_error request_id=%s", request_id)  # full detail stays in server logs
    return _error(503, "database_unavailable", "Data service temporarily unavailable.", request_id)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    request_id = uuid.uuid4().hex[:12]
    log.exception("unhandled_error request_id=%s", request_id)
    return _error(500, "internal_error", "Something went wrong. Please try again.", request_id)


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


app.include_router(providers.router)
app.include_router(triage.router)
app.include_router(chat.router)
app.include_router(hospital.router)
app.include_router(admin.router)
