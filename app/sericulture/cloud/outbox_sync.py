"""Outbox synchronization worker.

Drains pending events from the local SQLite outbox and transmits them to
AWS IoT Core in bounded batches over MQTT QoS 1.

SAFETY RULES:
  - Marks records ACKED only upon receiving durable application ACKs from AWS.
  - Network outages do NOT block the system; data queues durably in SQLite.
  - Recovers gracefully from restarts and network drops without duplicate delivery bugs.
"""

from __future__ import annotations

import json
import logging
import time
from threading import Event, Lock, Thread
from typing import Any

from sericulture.cloud.iot_client import CloudMqttManager
from sericulture.cloud.payloads import (
    DurableAckPayload,
    StandardMessagePayload,
    validate_mqtt_payload_size,
)
from sericulture.config import CloudConfig
from sericulture.storage.event_repository import EventRepository, OutboxRecord

logger = logging.getLogger(__name__)


class OutboxSyncWorker:
    """Background worker draining outbox queue to AWS IoT Core."""

    def __init__(
        self,
        config: CloudConfig,
        repository: EventRepository,
        mqtt_manager: CloudMqttManager,
        software_version: str = "1.0.0",
    ):
        self._config = config
        self._repo = repository
        self._mqtt = mqtt_manager
        self._software_version = software_version

        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._is_syncing = False

        # Register durable ACK listener
        self._mqtt.add_ack_listener(self._on_ack_received)

    def _channel_for_event_type(self, event_type: str) -> str:
        if event_type == "telemetry":
            return "telemetry"
        elif event_type == "health_snapshot":
            return "health"
        return "events"

    def _on_ack_received(self, payload_str: str) -> None:
        """Process durable ingestion ACK returned from the AWS cloud backend."""
        try:
            ack = DurableAckPayload.from_json(payload_str)
            logger.info(f"Received durable cloud ACK for event: {ack.event_id} (status: {ack.status})")
            self._repo.mark_outbox_acked([ack.event_id], acked_at=ack.timestamp_utc or None)
        except Exception as e:
            logger.warning(f"Failed to process cloud ACK payload '{payload_str}': {e}")

    def sync_batch(self, limit: int | None = None) -> int:
        """Attempt to send one batch of pending outbox events. Returns count sent."""
        if not self._config.enabled:
            return 0

        # Ensure MQTT connected
        if not self._mqtt.is_connected:
            if not self._mqtt.connect():
                return 0

        batch_size = limit or self._config.batch_size
        records: list[OutboxRecord] = self._repo.get_pending_outbox_batch(limit=batch_size)
        if not records:
            return 0

        sent_ids: list[str] = []
        failed_ids: list[str] = []

        for record in records:
            try:
                inner_payload = json.loads(record.payload_json)
                msg = StandardMessagePayload(
                    schema_version=record.schema_version,
                    event_id=record.event_id,
                    device_id=record.device_id,
                    farm_id=record.farm_id,
                    event_type=record.event_type,
                    timestamp_utc=record.timestamp_utc,
                    software_version=self._software_version,
                    payload=inner_payload,
                )
                payload_json = msg.to_json()

                if not validate_mqtt_payload_size(payload_json, self._config.max_payload_bytes):
                    logger.error(f"Event {record.event_id} exceeds MQTT max payload size! Skipping.")
                    failed_ids.append(record.event_id)
                    continue

                channel = self._channel_for_event_type(record.event_type)
                success = self._mqtt.publish_event(channel, payload_json)
                if success:
                    sent_ids.append(record.event_id)
                else:
                    failed_ids.append(record.event_id)
            except Exception as e:
                logger.error(f"Failed to process outbox record {record.event_id}: {e}")
                failed_ids.append(record.event_id)

        if sent_ids:
            # Mark IN_FLIGHT awaiting durable application ACK
            self._repo.mark_outbox_in_flight(sent_ids)
            logger.debug(f"Dispatched {len(sent_ids)} events to cloud; marked IN_FLIGHT.")

        if failed_ids:
            self._repo.mark_outbox_retry(failed_ids, "MQTT publish failed or network dropped", delay_sec=10.0)

        return len(sent_ids)

    def start(self, interval_s: float | None = None) -> None:
        """Start background sync thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            sync_int = interval_s or self._config.batch_interval_s
            self._thread = Thread(
                target=self._run_loop,
                args=(sync_int,),
                name="OutboxSyncWorker",
                daemon=True,
            )
            self._thread.start()

    def _run_loop(self, interval_s: float) -> None:
        while not self._stop_event.is_set():
            try:
                self.sync_batch()
            except Exception as e:
                logger.error(f"Unhandled error in outbox sync loop: {e}")

            self._stop_event.wait(timeout=interval_s)

    def stop(self) -> None:
        """Stop background worker."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
