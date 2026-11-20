# Changelog

All notable changes to this project are documented in this file.

## [1.1.0] - 2026-11-20
### Added
- Offline batch reconciliation with batch-level idempotency (`POST /devices/{device_id}/batches`).
- Sensor telemetry sampling gap detection (`GET /tags/{tag_key}/gaps`).
- Hardened CI pipeline with smoke tests, gitleaks, pip-audit, and load test benchmarks.
- Automated rollback runbook and failure injection test suite.

## [1.0.0] - 2026-11-05
### Added
- Initial industrial telemetry and threshold alarm service.
