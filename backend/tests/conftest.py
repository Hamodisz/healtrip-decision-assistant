"""Tests run against a real Postgres (separate `healtrip_test` database), not mocks:
the queries under test use Postgres-specific features (ARRAY ANY, timestamptz)."""
import os

import psycopg
import pytest

ADMIN_DSN = "postgresql://healtrip:healtrip@localhost:5433/healtrip"
os.environ["DATABASE_URL"] = "postgresql+psycopg://healtrip:healtrip@localhost:5433/healtrip_test"

with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
    if not conn.execute("SELECT 1 FROM pg_database WHERE datname='healtrip_test'").fetchone():
        conn.execute("CREATE DATABASE healtrip_test")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import reset_schema, seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    reset_schema()
    seed()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)
