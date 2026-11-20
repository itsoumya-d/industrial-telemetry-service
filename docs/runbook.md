# Operational Runbook & Rollback Procedure

## Rollback Procedure
If a production deployment exhibits anomalies or elevated error rates, follow this rollback procedure:

1. **Traffic Diversion & Container Revert**:
   Revert to the previous stable release container image tag:
   ```bash
   docker pull ghcr.io/itsoumya-d/industrial-telemetry-service:<previous-stable-tag>
   docker tag ghcr.io/itsoumya-d/industrial-telemetry-service:<previous-stable-tag> industrial-telemetry-service:active
   ```
2. **Database Migration Rollback**:
   If the release included database schema modifications:
   ```bash
   alembic downgrade -1
   ```
3. **Smoke Verification**:
   Execute the automated smoke verification script against the running container:
   ```bash
   ./scripts/smoke.sh http://localhost:8000
   ```
4. **Health Check & Ingestion Test**:
   Verify endpoint availability and database connectivity:
   ```bash
   curl -f http://localhost:8000/health
   ```
