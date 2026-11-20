from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from telemetry.api import create_app
from telemetry.db import Base
from telemetry.service import NotFoundError, TelemetryService, ValidationError

API_KEYS = {"admin-key": "admin", "operator-key": "operator", "viewer-key": "viewer"}
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    yield db
    db.close()


@pytest.fixture()
def client(session):
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    return TestClient(create_app(session_factory=factory, api_keys=API_KEYS, init_database=False))


def seed(service: TelemetryService, high=30.0, low=10.0):
    site = service.create_site("SITE-1", "Orchard", "Bay of Plenty")
    device = service.create_device("DEV-1", site.id, "Gateway")
    service.create_tag(site.id, "temp", "Temperature", "C", high_limit=high, low_limit=low)
    return site, device


def reading(value, minutes=0):
    return {"tag_key": "temp", "value": value, "ts": (T0 + timedelta(minutes=minutes)).isoformat()}


def test_create_site(session):
    row = TelemetryService(session).create_site("SITE-1", "Orchard")
    assert row.code == "SITE-1"


def test_duplicate_site_rejected(session):
    service = TelemetryService(session)
    service.create_site("SITE-1", "Orchard")
    with pytest.raises(ValidationError):
        service.create_site("SITE-1", "Other")


def test_create_device(session):
    service = TelemetryService(session)
    site, device = seed(service)
    assert device.device_id == "DEV-1"
    assert device.site_id == site.id


def test_create_tag(session):
    service = TelemetryService(session)
    site, _ = seed(service)
    from telemetry.tables import TagRow
    tag = session.query(TagRow).first()
    assert tag.tag_key == "temp"
    assert tag.high_limit == 30.0


def test_ingest_reading(session):
    service = TelemetryService(session)
    seed(service)
    result = service.ingest("DEV-1", [reading(20)])
    assert result["inserted"] == 1
    assert service.latest("temp")["value"] == 20


def test_duplicate_reading_is_idempotent(session):
    service = TelemetryService(session)
    seed(service)
    service.ingest("DEV-1", [reading(20)])
    result = service.ingest("DEV-1", [reading(20)])
    assert result == {"inserted": 0, "duplicates": 1, "alarms": []}


def test_history_and_summary(session):
    service = TelemetryService(session)
    seed(service)
    service.ingest("DEV-1", [reading(20, 0), reading(22, 5), reading(24, 10)])
    assert len(service.history("temp")) == 3
    summary = service.summary("temp")
    assert summary["count"] == 3
    assert summary["min"] == 20
    assert summary["max"] == 24
    assert summary["avg"] == 22


def test_high_alarm(session):
    service = TelemetryService(session)
    seed(service)
    result = service.ingest("DEV-1", [reading(35)])
    assert result["alarms"][0]["severity"] == "high"


def test_alarm_not_duplicated(session):
    service = TelemetryService(session)
    seed(service)
    service.ingest("DEV-1", [reading(35)])
    result = service.ingest("DEV-1", [reading(36, 1)])
    assert result["alarms"] == []


def test_alarm_auto_clears(session):
    service = TelemetryService(session)
    seed(service)
    service.ingest("DEV-1", [reading(35)])
    service.ingest("DEV-1", [reading(20, 1)])
    from telemetry.tables import AlarmRow
    assert session.query(AlarmRow).first().status == "cleared"


def test_low_alarm(session):
    service = TelemetryService(session)
    seed(service)
    result = service.ingest("DEV-1", [reading(5)])
    assert result["alarms"][0]["severity"] == "low"


def test_acknowledge_alarm(session):
    service = TelemetryService(session)
    seed(service)
    result = service.ingest("DEV-1", [reading(35)])
    alarm_id = result["alarms"][0]["id"]
    alarm = service.acknowledge(alarm_id, "checked")
    assert alarm.status == "acknowledged"


def test_clear_alarm(session):
    service = TelemetryService(session)
    seed(service)
    result = service.ingest("DEV-1", [reading(35)])
    alarm = service.clear(result["alarms"][0]["id"], "fixed")
    assert alarm.status == "cleared"


def test_offline_devices(session):
    service = TelemetryService(session)
    seed(service)
    devices = service.offline_devices(15, now=T0)
    assert devices[0]["device_id"] == "DEV-1"


def test_last_seen_updated(session):
    service = TelemetryService(session)
    seed(service)
    service.ingest("DEV-1", [reading(20)])
    devices = service.offline_devices(15, now=T0 + timedelta(minutes=10))
    assert devices == []


def test_audit(session):
    service = TelemetryService(session)
    seed(service)
    actions = [e["action"] for e in service.audit()]
    assert "site_created" in actions
    assert "tag_created" in actions


def test_unknown_tag(session):
    service = TelemetryService(session)
    seed(service)
    with pytest.raises(NotFoundError):
        service.ingest("DEV-1", [{"tag_key": "nope", "value": 1, "ts": T0.isoformat()}])


def test_api_auth_and_workflow(client):
    assert client.post("/sites", json={}).status_code == 401
    headers = {"X-API-Key": "operator-key"}
    site = client.post("/sites", json={"code": "SITE-1", "name": "Orchard"}, headers=headers).json()
    client.post("/devices", json={"device_id": "DEV-1", "site_id": site["id"]}, headers=headers)
    client.post("/tags", json={"site_id": site["id"], "tag_key": "temp", "name": "Temperature", "high_limit": 30}, headers=headers)
    result = client.post("/devices/DEV-1/readings", json={"readings": [{"tag_key": "temp", "value": 35, "ts": T0.isoformat()}]}, headers=headers).json()
    assert result["inserted"] == 1
    latest = client.get("/tags/temp/latest", headers={"X-API-Key": "viewer-key"}).json()
    assert latest["value"] == 35
    summary = client.get("/tags/temp/summary", headers={"X-API-Key": "viewer-key"}).json()
    assert summary["count"] == 1


def test_ingest_batch_first_time(session):
    service = TelemetryService(session)
    seed(service)
    batch_res = service.ingest_batch("DEV-1", "BATCH-001", [reading(21, 0), reading(22, 1)], client_sent_at=T0)
    assert batch_res["batch_id"] == "BATCH-001"
    assert batch_res["status"] == "processed"
    assert batch_res["inserted"] == 2
    assert batch_res["idempotent"] is False


def test_ingest_batch_idempotent_retry(session):
    service = TelemetryService(session)
    seed(service)
    service.ingest_batch("DEV-1", "BATCH-002", [reading(25, 0)])
    # Retry same batch
    retry_res = service.ingest_batch("DEV-1", "BATCH-002", [reading(25, 0)])
    assert retry_res["batch_id"] == "BATCH-002"
    assert retry_res["status"] == "already_processed"
    assert retry_res["idempotent"] is True


def test_ingest_batch_empty_id_rejected(session):
    service = TelemetryService(session)
    seed(service)
    with pytest.raises(ValidationError):
        service.ingest_batch("DEV-1", "   ", [reading(20)])


def test_detect_gaps_none_when_continuous(session):
    service = TelemetryService(session)
    seed(service)
    # Continuous readings every 60 seconds
    service.ingest("DEV-1", [reading(20, 0), reading(21, 1), reading(22, 2)])
    gaps = service.detect_gaps("temp", expected_interval_seconds=60)
    assert gaps == []


def test_detect_gaps_identifies_missing_interval(session):
    service = TelemetryService(session)
    seed(service)
    # Readings at T0 and T0 + 10 minutes (gap of 600s with expected interval 60s)
    service.ingest("DEV-1", [reading(20, 0), reading(22, 10)])
    gaps = service.detect_gaps("temp", expected_interval_seconds=60)
    assert len(gaps) == 1
    assert gaps[0]["gap_seconds"] == 600.0
    assert gaps[0]["estimated_missing"] == 9


def test_api_batch_and_gap_endpoints(client):
    headers = {"X-API-Key": "operator-key"}
    site = client.post("/sites", json={"code": "SITE-B", "name": "Pasture"}, headers=headers).json()
    client.post("/devices", json={"device_id": "DEV-B", "site_id": site["id"]}, headers=headers)
    client.post("/tags", json={"site_id": site["id"], "tag_key": "vibe", "name": "Vibration"}, headers=headers)

    payload = {
        "batch_id": "BATCH-API-1",
        "readings": [
            {"tag_key": "vibe", "value": 1.2, "ts": T0.isoformat()},
            {"tag_key": "vibe", "value": 1.8, "ts": (T0 + timedelta(minutes=5)).isoformat()}
        ],
        "client_sent_at": T0.isoformat()
    }
    res = client.post("/devices/DEV-B/batches", json=payload, headers=headers).json()
    assert res["status"] == "processed"
    assert res["inserted"] == 2

    # Verify idempotency over API
    res_retry = client.post("/devices/DEV-B/batches", json=payload, headers=headers).json()
    assert res_retry["status"] == "already_processed"
    assert res_retry["idempotent"] is True

    # Test gaps API endpoint
    gap_res = client.get("/tags/vibe/gaps?expected_interval_seconds=60", headers={"X-API-Key": "viewer-key"}).json()
    assert gap_res["tag_key"] == "vibe"
    assert len(gap_res["gaps"]) == 1
    assert gap_res["gaps"][0]["gap_seconds"] == 300.0
