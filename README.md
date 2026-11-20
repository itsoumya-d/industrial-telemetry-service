# Industrial Telemetry Service

Production-oriented telemetry and alarm backend for industrial monitoring:
sites, devices, tags, idempotent reading ingestion, threshold alarms, alarm
acknowledgement/clearing, offline-device detection, history, summaries and audit.

## Why this exists

Industrial monitoring is decided by data integrity and alarm behaviour. The
hard parts are duplicate gateway retries, out-of-order timestamps, threshold
debouncing, alarm lifecycle and proving what happened. This service models
those explicitly with tested rules.

## Features

- Sites, gateways/devices and measurement tags
- High/low limits per tag, with unit metadata
- Idempotent ingestion keyed by device + tag + timestamp
- Offline batch reconciliation with batch-level idempotency (`POST /devices/{device_id}/batches`)
- Missing telemetry sampling gap detection (`GET /tags/{tag_key}/gaps`)
- Duplicate readings counted, not double-stored
- Automatic high/low alarms and automatic clearing when values return to range
- No duplicate active alarm per tag
- Alarm acknowledgement and manual clear with actor/audit
- Latest value, history window and min/max/avg summary
- Offline-device detection from `last_seen`
- Append-only audit events
- API-key RBAC: viewer, operator, admin
- PostgreSQL or SQLite, SQLAlchemy 2, Alembic, Docker, GitHub Actions

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
DATABASE_URL=sqlite:///./telemetry.db .venv/bin/alembic upgrade head
API_KEYS='{"dev-admin":"admin","dev-operator":"operator","dev-viewer":"viewer"}' \
  .venv/bin/uvicorn telemetry.api:app --reload
```

## Test

```bash
.venv/bin/pytest
```

## Honest scope

This is a real, tested backend service, not a production deployment. It does
not speak Modbus, OPC UA, MQTT or PLC protocols, does not run a time-series
database, and does not dispatch SMS/email alerts. Devices post readings over
HTTP. The README does not claim production usage.
