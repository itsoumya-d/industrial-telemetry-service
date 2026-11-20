from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from .auth import load_api_keys, require_role
from .db import SessionLocal, init_db
from .service import ConflictError, NotFoundError, TelemetryService, ValidationError


class SiteIn(BaseModel):
    code: str
    name: str
    region: str = ""


class DeviceIn(BaseModel):
    device_id: str
    site_id: int
    name: str = ""


class TagIn(BaseModel):
    site_id: int
    tag_key: str
    name: str
    unit: str = ""
    high_limit: float | None = None
    low_limit: float | None = None


class ReadingIn(BaseModel):
    tag_key: str
    value: float
    ts: datetime
    quality: str = "good"


class IngestIn(BaseModel):
    readings: list[ReadingIn]


class BatchIngestIn(BaseModel):
    batch_id: str
    readings: list[ReadingIn]
    client_sent_at: datetime | None = None


class AlarmNoteIn(BaseModel):
    note: str = ""


def create_app(session_factory: sessionmaker = SessionLocal, api_keys: dict[str, str] | None = None, init_database: bool = True) -> FastAPI:
    if init_database:
        init_db()
    keys = api_keys or load_api_keys()
    app = FastAPI(title="Industrial telemetry service", version="1.0.0")

    def db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    viewer = require_role("viewer", keys)
    operator = require_role("operator", keys)

    def handle(exc: Exception):
        if isinstance(exc, NotFoundError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, ConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sites")
    def site(payload: SiteIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            row = TelemetryService(session, actor).create_site(payload.code, payload.name, payload.region)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "code": row.code}

    @app.post("/devices")
    def device(payload: DeviceIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            row = TelemetryService(session, actor).create_device(payload.device_id, payload.site_id, payload.name)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "device_id": row.device_id}

    @app.post("/tags")
    def tag(payload: TagIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            row = TelemetryService(session, actor).create_tag(payload.site_id, payload.tag_key, payload.name, payload.unit, payload.high_limit, payload.low_limit)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "tag_key": row.tag_key}

    @app.post("/devices/{device_id}/readings")
    def ingest(device_id: str, payload: IngestIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            return TelemetryService(session, actor).ingest(device_id, [r.model_dump() for r in payload.readings])
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.post("/devices/{device_id}/batches")
    def batch(device_id: str, payload: BatchIngestIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            return TelemetryService(session, actor).ingest_batch(device_id, payload.batch_id, [r.model_dump() for r in payload.readings], payload.client_sent_at)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/tags/{tag_key}/latest")
    def latest(tag_key: str, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return TelemetryService(session).latest(tag_key)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/tags/{tag_key}/gaps")
    def gaps(tag_key: str, expected_interval_seconds: int = 60, since: datetime | None = None, until: datetime | None = None, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return {"tag_key": tag_key, "gaps": TelemetryService(session).detect_gaps(tag_key, expected_interval_seconds, since, until)}
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/tags/{tag_key}/history")
    def history(tag_key: str, since: datetime | None = None, until: datetime | None = None, limit: int = 500, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return {"readings": TelemetryService(session).history(tag_key, since, until, limit)}
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/tags/{tag_key}/summary")
    def summary(tag_key: str, since: datetime | None = None, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return TelemetryService(session).summary(tag_key, since)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.post("/alarms/{alarm_id}/acknowledge")
    def acknowledge(alarm_id: int, payload: AlarmNoteIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            row = TelemetryService(session, actor).acknowledge(alarm_id, payload.note)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "status": row.status}

    @app.post("/alarms/{alarm_id}/clear")
    def clear(alarm_id: int, payload: AlarmNoteIn, session: Session = Depends(db_session), actor: str = Depends(operator)) -> dict[str, Any]:
        try:
            row = TelemetryService(session, actor).clear(alarm_id, payload.note)
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)
        return {"id": row.id, "status": row.status}

    @app.get("/devices/offline")
    def offline(stale_minutes: int = 15, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        try:
            return {"devices": TelemetryService(session).offline_devices(stale_minutes)}
        except (NotFoundError, ConflictError, ValidationError) as exc:
            handle(exc)

    @app.get("/audit")
    def audit(session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        return {"audit": TelemetryService(session).audit()}

    return app


app = create_app()
