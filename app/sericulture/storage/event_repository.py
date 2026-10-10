"""Event repository with transactional outbox pattern.

Every operational event, telemetry sample, actuator action, AI inference result,
and health snapshot is durably written to its local business table AND the outbox
table in a single atomic SQLite transaction.

SAFETY:
  - Cloud failure does NOT stop local writes.
  - Events are NEVER deleted until durably acknowledged by the cloud and past retention.
  - Idempotent writes protect against duplicate event processing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sericulture.config import AppConfig
from sericulture.event_types import (
    ActuatorResult,
    EventType,
    SHT40Reading,
    SyncStatus,
)
from sericulture.storage.database import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class OutboxRecord:
    """An un-synchronized event from the outbox table."""
    id: int
    event_id: str
    device_id: str
    farm_id: str
    event_type: str
    schema_version: int
    timestamp_utc: str
    payload_json: str
    sync_status: str
    attempt_count: int
    created_at: str


class EventRepository:
    """Repository managing local tables and transactional outbox queues."""

    def __init__(self, db_manager: DatabaseManager, device_id: str, farm_id: str):
        self._db = db_manager
        self._device_id = device_id
        self._farm_id = farm_id

    def record_telemetry(
        self,
        reading: SHT40Reading,
        fan_on: bool,
        mist_on: bool,
        light_on: bool,
        float_state: str,
        mains_present: bool,
        camera_ok: bool,
        hailo_ok: bool,
        event_id: str | None = None,
    ) -> str:
        """Persist SHT40 telemetry and enqueue in outbox atomically."""
        eid = event_id or str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()

        payload = {
            "temperature_c": reading.temperature_c,
            "relative_humidity_pct": reading.relative_humidity_pct,
            "sensor_health": reading.health.value,
            "fan_commanded_on": fan_on,
            "mist_commanded_on": mist_on,
            "night_light_commanded_on": light_on,
            "float_switch_state": float_state,
            "mains_present": mains_present,
            "camera_status": "OK" if camera_ok else "FAULT",
            "hailo_status": "OK" if hailo_ok else "FAULT",
        }
        payload_json = json.dumps(payload)

        conn = self._db.get_connection()
        try:
            with conn:
                # 1. Telemetry table
                conn.execute(
                    """
                    INSERT INTO telemetry (
                        event_id, device_id, farm_id, timestamp_utc,
                        temperature_c, relative_humidity_pct, sensor_health, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id, now_utc,
                        reading.temperature_c, reading.relative_humidity_pct,
                        reading.health.value, now_utc
                    ),
                )
                # 2. Outbox table
                conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id, device_id, farm_id, event_type, schema_version,
                        timestamp_utc, payload_json, sync_status, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'PENDING', ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id,
                        EventType.TELEMETRY.value, now_utc, payload_json, now_utc
                    ),
                )
            return eid
        except Exception as e:
            logger.error(f"Failed to record telemetry transaction: {e}")
            raise

    def record_device_event(
        self,
        event_type: EventType,
        details: dict[str, Any],
        event_id: str | None = None,
    ) -> str:
        """Persist system / state / error event and enqueue in outbox atomically."""
        eid = event_id or str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(details)

        conn = self._db.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO device_events (
                        event_id, device_id, farm_id, event_type,
                        timestamp_utc, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id,
                        event_type.value, now_utc, payload_json, now_utc
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id, device_id, farm_id, event_type, schema_version,
                        timestamp_utc, payload_json, sync_status, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'PENDING', ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id,
                        event_type.value, now_utc, payload_json, now_utc
                    ),
                )
            return eid
        except Exception as e:
            logger.error(f"Failed to record device event {event_type.value}: {e}")
            raise

    def record_actuator_event(
        self,
        result: ActuatorResult,
        event_id: str | None = None,
    ) -> str:
        """Persist actuator state change and enqueue in outbox atomically."""
        eid = event_id or str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()

        payload = {
            "load": result.request.load.value,
            "desired_state": result.request.desired.value,
            "actual_command": result.actual_command.value,
            "allowed": result.allowed,
            "reason": result.request.reason,
            "source": result.request.source,
            "rejection_reason": result.rejection_reason,
            "feedback_status": result.relay_feedback,
        }
        payload_json = json.dumps(payload)

        conn = self._db.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO actuator_events (
                        event_id, device_id, farm_id, timestamp_utc,
                        load, commanded_state, reason, source, feedback_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id, now_utc,
                        result.request.load.value, result.actual_command.value,
                        result.request.reason, result.request.source,
                        result.relay_feedback, now_utc
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id, device_id, farm_id, event_type, schema_version,
                        timestamp_utc, payload_json, sync_status, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'PENDING', ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id,
                        EventType.ACTUATOR_CHANGE.value, now_utc, payload_json, now_utc
                    ),
                )
            return eid
        except Exception as e:
            logger.error(f"Failed to record actuator event: {e}")
            raise

    def record_ai_event(
        self,
        model_version: str,
        tracked_count: int,
        avg_movement_score: float,
        inactive_count: int,
        status: str,
        detections: list[dict[str, Any]] | None = None,
        event_id: str | None = None,
    ) -> str:
        """Persist vision / tracking results and enqueue in outbox atomically."""
        eid = event_id or str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()

        payload = {
            "model_version": model_version,
            "tracked_count": tracked_count,
            "avg_movement_score": avg_movement_score,
            "inactive_count": inactive_count,
            "status": status,
            "detections": detections or [],
        }
        payload_json = json.dumps(payload)

        conn = self._db.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO ai_events (
                        event_id, device_id, farm_id, timestamp_utc,
                        model_version, tracked_count, avg_movement_score,
                        inactive_count, status, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id, now_utc,
                        model_version, tracked_count, avg_movement_score,
                        inactive_count, status, payload_json, now_utc
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id, device_id, farm_id, event_type, schema_version,
                        timestamp_utc, payload_json, sync_status, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'PENDING', ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id,
                        EventType.AI_DETECTION.value, now_utc, payload_json, now_utc
                    ),
                )
            return eid
        except Exception as e:
            logger.error(f"Failed to record AI event: {e}")
            raise

    def record_health_snapshot(
        self,
        cpu_usage_pct: float,
        ram_usage_pct: float,
        disk_used_pct: float,
        cpu_temp_c: float,
        uptime_s: float,
        service_status: str,
        components_summary: dict[str, Any],
        event_id: str | None = None,
    ) -> str:
        """Persist periodic system health snapshot and enqueue in outbox atomically."""
        eid = event_id or str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()

        payload = {
            "cpu_usage_pct": cpu_usage_pct,
            "ram_usage_pct": ram_usage_pct,
            "disk_used_pct": disk_used_pct,
            "cpu_temp_c": cpu_temp_c,
            "uptime_s": uptime_s,
            "service_status": service_status,
            "components": components_summary,
        }
        payload_json = json.dumps(payload)

        conn = self._db.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO health_snapshots (
                        event_id, device_id, farm_id, timestamp_utc,
                        cpu_usage_pct, ram_usage_pct, disk_used_pct, cpu_temp_c,
                        uptime_s, service_status, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id, now_utc,
                        cpu_usage_pct, ram_usage_pct, disk_used_pct, cpu_temp_c,
                        uptime_s, service_status, payload_json, now_utc
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id, device_id, farm_id, event_type, schema_version,
                        timestamp_utc, payload_json, sync_status, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'PENDING', ?)
                    """,
                    (
                        eid, self._device_id, self._farm_id,
                        EventType.HEALTH_SNAPSHOT.value, now_utc, payload_json, now_utc
                    ),
                )
            return eid
        except Exception as e:
            logger.error(f"Failed to record health snapshot: {e}")
            raise

    # ──────────────────────────────────────────────────────────────────
    # Outbox queries & state transitions
    # ──────────────────────────────────────────────────────────────────

    def get_pending_outbox_batch(self, limit: int = 50) -> list[OutboxRecord]:
        """Fetch oldest pending or retry-eligible outbox records up to limit."""
        conn = self._db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, event_id, device_id, farm_id, event_type, schema_version,
                   timestamp_utc, payload_json, sync_status, attempt_count, created_at
            FROM outbox
            WHERE sync_status = 'PENDING'
               OR (sync_status = 'RETRY' AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now')))
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        )
        records = []
        for row in cursor.fetchall():
            records.append(
                OutboxRecord(
                    id=row["id"],
                    event_id=row["event_id"],
                    device_id=row["device_id"],
                    farm_id=row["farm_id"],
                    event_type=row["event_type"],
                    schema_version=row["schema_version"],
                    timestamp_utc=row["timestamp_utc"],
                    payload_json=row["payload_json"],
                    sync_status=row["sync_status"],
                    attempt_count=row["attempt_count"],
                    created_at=row["created_at"],
                )
            )
        return records

    def mark_outbox_in_flight(self, event_ids: list[str]) -> None:
        """Mark outbox items as IN_FLIGHT when being dispatched to MQTT."""
        if not event_ids:
            return
        conn = self._db.get_connection()
        with conn:
            placeholders = ",".join("?" for _ in event_ids)
            conn.execute(
                f"""
                UPDATE outbox
                SET sync_status = 'IN_FLIGHT', attempt_count = attempt_count + 1
                WHERE event_id IN ({placeholders})
                """,
                event_ids,
            )

    def mark_outbox_acked(self, event_ids: list[str], acked_at: str | None = None) -> None:
        """Mark outbox items as ACKED upon receiving durable cloud confirmation."""
        if not event_ids:
            return
        ack_time = acked_at or datetime.now(timezone.utc).isoformat()
        conn = self._db.get_connection()
        with conn:
            placeholders = ",".join("?" for _ in event_ids)
            conn.execute(
                f"""
                UPDATE outbox
                SET sync_status = 'ACKED', acked_at = ?
                WHERE event_id IN ({placeholders})
                """,
                [ack_time] + event_ids,
            )

    def mark_outbox_retry(
        self,
        event_ids: list[str],
        error_msg: str,
        delay_sec: float = 5.0,
    ) -> None:
        """Reset failed items to RETRY with backoff timestamp."""
        if not event_ids:
            return
        conn = self._db.get_connection()
        with conn:
            placeholders = ",".join("?" for _ in event_ids)
            conn.execute(
                f"""
                UPDATE outbox
                SET sync_status = 'RETRY',
                    last_error = ?,
                    next_attempt_at = datetime('now', '+{int(delay_sec)} seconds')
                WHERE event_id IN ({placeholders})
                """,
                [error_msg] + event_ids,
            )

    def cleanup_acked_events(self, retention_days: int = 7) -> int:
        """Purge ACKED events older than retention_days. NEVER deletes unsynced events!"""
        conn = self._db.get_connection()
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                DELETE FROM outbox
                WHERE sync_status = 'ACKED'
                  AND created_at <= datetime('now', '-{retention_days} days')
                """
            )
            return cursor.rowcount

    def get_outbox_counts(self) -> dict[str, int]:
        """Summary of outbox queue sizes by status."""
        conn = self._db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sync_status, COUNT(*) as cnt FROM outbox GROUP BY sync_status;")
        counts = {row["sync_status"]: row["cnt"] for row in cursor.fetchall()}
        return {
            "pending": counts.get("PENDING", 0),
            "in_flight": counts.get("IN_FLIGHT", 0),
            "acked": counts.get("ACKED", 0),
            "retry": counts.get("RETRY", 0),
            "total": sum(counts.values()),
        }
