"""Pytest setup: isolated SQLite DB for the whole test session."""
import os
import sys
import tempfile

DB_PATH = os.path.join(tempfile.mkdtemp(), "truckroute_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["BETA_ALL_PREMIUM"] = "true"
os.environ["ADMIN_EMAILS"] = "admin@examplemail.com"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app_main  # noqa: E402
import models  # noqa: E402
from database import SessionLocal  # noqa: E402


@pytest.fixture(scope="session")
def client():
    return TestClient(app_main.app)


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


def make_user(client, email, password="password123", name="Test Driver"):
    r = client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": name})
    assert r.status_code == 201, r.text
    return r.json()


def auth_headers(client, email, password="password123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}
