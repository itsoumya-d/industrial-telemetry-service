from __future__ import annotations

from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from telemetry.db import Base
from telemetry.service import NotFoundError, TelemetryService, ValidationError

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    yield db
    db.close()


def test_failure_invalid_device_ingest(session):
    service = TelemetryService(session)
    with pytest.raises(NotFoundError):
        service.ingest("NONEXISTENT-DEV", [{"tag_key": "temp", "value": 25, "ts": T0.isoformat()}])


def test_failure_malformed_reading_data(session):
    service = TelemetryService(session)
    site = service.create_site("SITE-FAIL", "Test Site")
    service.create_device("DEV-FAIL", site.id)
    service.create_tag(site.id, "temp", "Temperature")

    with pytest.raises(ValidationError):
        service.ingest("DEV-FAIL", [{"tag_key": "temp", "value": "not-a-number", "ts": T0.isoformat()}])


def test_failure_empty_readings(session):
    service = TelemetryService(session)
    site = service.create_site("SITE-FAIL2", "Test Site 2")
    service.create_device("DEV-FAIL2", site.id)
    with pytest.raises(ValidationError):
        service.ingest("DEV-FAIL2", [])


def test_failure_inactive_device_rejected(session):
    service = TelemetryService(session)
    site = service.create_site("SITE-INACTIVE", "Inactive Site")
    device = service.create_device("DEV-INACT", site.id)
    device.active = False
    session.commit()

    with pytest.raises(ValidationError):
        service.ingest("DEV-INACT", [{"tag_key": "temp", "value": 20, "ts": T0.isoformat()}])
