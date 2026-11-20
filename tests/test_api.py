from __future__ import annotations

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from telemetry.api import create_app
from telemetry.db import Base

API_KEYS = {"admin-key": "admin", "operator-key": "operator", "viewer-key": "viewer"}
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return TestClient(create_app(session_factory=factory, api_keys=API_KEYS, init_database=False))


def test_api_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_api_unauthorized_access(client):
    res = client.post("/sites", json={"code": "S1", "name": "Site 1"})
    assert res.status_code == 401


def test_api_viewer_role_restrictions(client):
    headers = {"X-API-Key": "viewer-key"}
    res = client.post("/sites", json={"code": "S1", "name": "Site 1"}, headers=headers)
    assert res.status_code == 403


def test_api_audit_log_endpoint(client):
    headers = {"X-API-Key": "operator-key"}
    client.post("/sites", json={"code": "SAUDIT", "name": "Audit Site"}, headers=headers)
    res = client.get("/audit", headers={"X-API-Key": "viewer-key"})
    assert res.status_code == 200
    assert len(res.json()["audit"]) >= 1
