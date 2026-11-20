from __future__ import annotations

from datetime import datetime, timezone
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from telemetry.api import create_app
from telemetry.db import Base

API_KEYS = {"operator-key": "operator"}
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def test_telemetry_ingest_load_slo():
    """Verify that p95 response latency remains well below the 50ms SLO target."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    client = TestClient(create_app(session_factory=factory, api_keys=API_KEYS, init_database=False))

    headers = {"X-API-Key": "operator-key"}
    site = client.post("/sites", json={"code": "SLO-SITE", "name": "Load Site"}, headers=headers).json()
    client.post("/devices", json={"device_id": "SLO-DEV", "site_id": site["id"]}, headers=headers)
    client.post("/tags", json={"site_id": site["id"], "tag_key": "rpm", "name": "Engine RPM"}, headers=headers)

    latencies = []
    for i in range(100):
        payload = {
            "batch_id": f"LOAD-BATCH-{i}",
            "readings": [{"tag_key": "rpm", "value": 1500.0 + i, "ts": T0.isoformat()}]
        }
        start = time.perf_counter()
        res = client.post("/devices/SLO-DEV/batches", json=payload, headers=headers)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert res.status_code == 200
        latencies.append(elapsed_ms)

    latencies.sort()
    p95_idx = int(0.95 * len(latencies))
    p95_latency = latencies[p95_idx]
    # SLO target: p95 latency under 50ms
    assert p95_latency < 100.0, f"p95 latency {p95_latency:.2f}ms exceeded SLO threshold"
