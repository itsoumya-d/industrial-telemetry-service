from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .tables import AlarmRow, AuditRow, DeviceRow, ReadingRow, SiteRow, TagRow


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ConflictError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class TelemetryService:
    def __init__(self, session: Session, actor: str = "system") -> None:
        self.session = session
        self.actor = actor

    def _audit(self, action: str, reference: str, detail: str = "") -> None:
        self.session.add(AuditRow(action=action, actor=self.actor, reference=reference, detail=detail))

    def create_site(self, code: str, name: str, region: str = "") -> SiteRow:
        if not code.strip() or not name.strip():
            raise ValidationError("site code and name are required")
        if self.session.scalar(select(SiteRow).where(SiteRow.code == code)):
            raise ValidationError("site already exists")
        row = SiteRow(code=code, name=name, region=region)
        self.session.add(row)
        self.session.flush()
        self._audit("site_created", code)
        self.session.commit()
        return row

    def create_device(self, device_id: str, site_id: int, name: str = "") -> DeviceRow:
        if not device_id.strip():
            raise ValidationError("device_id is required")
        if self.session.get(SiteRow, site_id) is None:
            raise NotFoundError("site not found")
        if self.session.scalar(select(DeviceRow).where(DeviceRow.device_id == device_id)):
            raise ValidationError("device already exists")
        row = DeviceRow(device_id=device_id, site_id=site_id, name=name)
        self.session.add(row)
        self.session.flush()
        self._audit("device_created", device_id)
        self.session.commit()
        return row

    def create_tag(self, site_id: int, tag_key: str, name: str, unit: str = "", high_limit: float | None = None, low_limit: float | None = None) -> TagRow:
        if not tag_key.strip() or not name.strip():
            raise ValidationError("tag_key and name are required")
        if self.session.get(SiteRow, site_id) is None:
            raise NotFoundError("site not found")
        if self.session.scalar(select(TagRow).where(TagRow.site_id == site_id, TagRow.tag_key == tag_key)):
            raise ValidationError("tag already exists for site")
        if high_limit is not None and low_limit is not None and low_limit >= high_limit:
            raise ValidationError("low_limit must be below high_limit")
        row = TagRow(site_id=site_id, tag_key=tag_key, name=name, unit=unit, high_limit=high_limit, low_limit=low_limit)
        self.session.add(row)
        self.session.flush()
        self._audit("tag_created", f"{site_id}/{tag_key}")
        self.session.commit()
        return row

    def ingest(self, device_id: str, readings: list[dict]) -> dict:
        device = self.session.scalar(select(DeviceRow).where(DeviceRow.device_id == device_id))
        if device is None:
            raise NotFoundError("device not found")
        if not device.active:
            raise ValidationError("device is inactive")
        if not readings:
            raise ValidationError("readings are required")
        inserted = 0
        duplicates = 0
        alarms = []
        latest_seen = None
        for raw in readings:
            tag_key = str(raw.get("tag_key") or "").strip()
            try:
                value = float(raw["value"])
                ts = as_utc(datetime.fromisoformat(str(raw["ts"]).replace("Z", "+00:00")))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(f"invalid reading: {raw}") from exc
            tag = self.session.scalar(select(TagRow).where(TagRow.site_id == device.site_id, TagRow.tag_key == tag_key))
            if tag is None:
                raise NotFoundError(f"tag not found for site: {tag_key}")
            existing = self.session.scalar(select(ReadingRow).where(ReadingRow.device_id == device_id, ReadingRow.tag_key == tag_key, ReadingRow.ts == ts))
            if existing is not None:
                duplicates += 1
                continue
            self.session.add(ReadingRow(device_id=device_id, tag_key=tag_key, ts=ts, value=value, quality=str(raw.get("quality", "good"))))
            inserted += 1
            latest_seen = max(latest_seen, ts) if latest_seen else ts
            alarm = self._evaluate(tag, value, ts)
            if alarm:
                alarms.append(alarm)
        if latest_seen:
            device.last_seen = max(as_utc(device.last_seen), latest_seen) if device.last_seen else latest_seen
        self._audit("readings_ingested", device_id, f"inserted={inserted} duplicates={duplicates}")
        self.session.commit()
        return {"inserted": inserted, "duplicates": duplicates, "alarms": alarms}

    def ingest_batch(self, device_id: str, batch_id: str, readings: list[dict], client_sent_at: datetime | None = None) -> dict:
        batch_id = (batch_id or "").strip()
        if not batch_id:
            raise ValidationError("batch_id is required")
        batch_ref = f"{device_id}:{batch_id}"
        prior = self.session.scalar(select(AuditRow).where(AuditRow.action == "batch_ingested", AuditRow.reference == batch_ref))
        if prior is not None:
            return {
                "batch_id": batch_id,
                "status": "already_processed",
                "detail": prior.detail,
                "idempotent": True,
            }
        drift_note = ""
        if client_sent_at is not None:
            delta = abs((utcnow() - as_utc(client_sent_at)).total_seconds())
            if delta > 300:
                drift_note = f" clock_drift_sec={round(delta, 1)}"
        ingest_res = self.ingest(device_id, readings)
        detail = f"inserted={ingest_res['inserted']} duplicates={ingest_res['duplicates']}{drift_note}"
        self._audit("batch_ingested", batch_ref, detail)
        self.session.commit()
        return {
            "batch_id": batch_id,
            "status": "processed",
            "inserted": ingest_res["inserted"],
            "duplicates": ingest_res["duplicates"],
            "alarms": ingest_res["alarms"],
            "idempotent": False,
        }

    def _evaluate(self, tag: TagRow, value: float, ts: datetime) -> dict | None:
        breach = None
        if tag.high_limit is not None and value > tag.high_limit:
            breach = ("high", f"{tag.tag_key} above {tag.high_limit}{tag.unit}")
        elif tag.low_limit is not None and value < tag.low_limit:
            breach = ("low", f"{tag.tag_key} below {tag.low_limit}{tag.unit}")
        active = self.session.scalar(select(AlarmRow).where(AlarmRow.site_id == tag.site_id, AlarmRow.tag_key == tag.tag_key, AlarmRow.status != "cleared"))
        if breach is None:
            if active is not None:
                active.status = "cleared"
                active.cleared_at = ts
                self._audit("alarm_cleared", tag.tag_key)
            return None
        severity, message = breach
        if active is not None:
            return None
        alarm = AlarmRow(site_id=tag.site_id, tag_key=tag.tag_key, severity=severity, message=message, raised_at=ts)
        self.session.add(alarm)
        self.session.flush()
        self._audit("alarm_raised", tag.tag_key, message)
        return {"id": alarm.id, "tag_key": tag.tag_key, "severity": severity, "message": message}

    def latest(self, tag_key: str) -> dict:
        row = self.session.scalar(select(ReadingRow).where(ReadingRow.tag_key == tag_key).order_by(ReadingRow.ts.desc(), ReadingRow.id.desc()))
        if row is None:
            raise NotFoundError("no readings for tag")
        return {"tag_key": row.tag_key, "value": row.value, "ts": row.ts.isoformat(), "quality": row.quality}

    def history(self, tag_key: str, since: datetime | None = None, until: datetime | None = None, limit: int = 500) -> list[dict]:
        if limit < 1 or limit > 5000:
            raise ValidationError("limit must be between 1 and 5000")
        query = select(ReadingRow).where(ReadingRow.tag_key == tag_key)
        if since:
            query = query.where(ReadingRow.ts >= as_utc(since))
        if until:
            query = query.where(ReadingRow.ts <= as_utc(until))
        rows = self.session.scalars(query.order_by(ReadingRow.ts).limit(limit)).all()
        return [{"value": r.value, "ts": r.ts.isoformat(), "quality": r.quality} for r in rows]

    def summary(self, tag_key: str, since: datetime | None = None) -> dict:
        query = select(func.count(ReadingRow.id), func.min(ReadingRow.value), func.max(ReadingRow.value), func.avg(ReadingRow.value)).where(ReadingRow.tag_key == tag_key)
        if since:
            query = query.where(ReadingRow.ts >= as_utc(since))
        count, minimum, maximum, average = self.session.execute(query).one()
        if not count:
            raise NotFoundError("no readings for tag")
        return {"tag_key": tag_key, "count": count, "min": minimum, "max": maximum, "avg": round(average, 4)}

    def detect_gaps(self, tag_key: str, expected_interval_seconds: int, since: datetime | None = None, until: datetime | None = None, tolerance_ratio: float = 1.5) -> list[dict]:
        if expected_interval_seconds <= 0:
            raise ValidationError("expected_interval_seconds must be positive")
        query = select(ReadingRow).where(ReadingRow.tag_key == tag_key)
        if since:
            query = query.where(ReadingRow.ts >= as_utc(since))
        if until:
            query = query.where(ReadingRow.ts <= as_utc(until))
        rows = self.session.scalars(query.order_by(ReadingRow.ts)).all()
        if len(rows) < 2:
            return []
        threshold = expected_interval_seconds * tolerance_ratio
        gaps = []
        for i in range(1, len(rows)):
            prev_ts = as_utc(rows[i - 1].ts)
            curr_ts = as_utc(rows[i].ts)
            delta_sec = (curr_ts - prev_ts).total_seconds()
            if delta_sec > threshold:
                est_missing = max(1, int(delta_sec // expected_interval_seconds) - 1)
                gaps.append({
                    "from_ts": prev_ts.isoformat(),
                    "to_ts": curr_ts.isoformat(),
                    "gap_seconds": round(delta_sec, 2),
                    "estimated_missing": est_missing,
                })
        return gaps

    def acknowledge(self, alarm_id: int, note: str = "") -> AlarmRow:
        alarm = self.session.get(AlarmRow, alarm_id)
        if alarm is None:
            raise NotFoundError("alarm not found")
        if alarm.status == "cleared":
            raise ValidationError("cannot acknowledge a cleared alarm")
        if alarm.status == "acknowledged":
            return alarm
        alarm.status = "acknowledged"
        alarm.acknowledged_at = utcnow()
        alarm.acknowledged_by = self.actor
        self._audit("alarm_acknowledged", str(alarm.id), note)
        self.session.commit()
        return alarm

    def clear(self, alarm_id: int, note: str = "") -> AlarmRow:
        alarm = self.session.get(AlarmRow, alarm_id)
        if alarm is None:
            raise NotFoundError("alarm not found")
        if alarm.status == "cleared":
            return alarm
        alarm.status = "cleared"
        alarm.cleared_at = utcnow()
        self._audit("alarm_cleared", str(alarm.id), note)
        self.session.commit()
        return alarm

    def offline_devices(self, stale_minutes: int = 15, now: datetime | None = None) -> list[dict]:
        if stale_minutes <= 0:
            raise ValidationError("stale_minutes must be positive")
        cutoff = (now or utcnow()) - timedelta(minutes=stale_minutes)
        rows = self.session.scalars(select(DeviceRow).where(DeviceRow.active.is_(True))).all()
        return [
            {"device_id": d.device_id, "site_id": d.site_id, "last_seen": d.last_seen.isoformat() if d.last_seen else None}
            for d in rows
            if d.last_seen is None or as_utc(d.last_seen) < cutoff
        ]

    def audit(self) -> list[dict]:
        rows = self.session.scalars(select(AuditRow).order_by(AuditRow.id)).all()
        return [{"at": r.at.isoformat(), "action": r.action, "actor": r.actor, "reference": r.reference, "detail": r.detail} for r in rows]
