"""Unit tests for AWS IoT Core sync and durable ACK contract."""

import pytest
import json
from sericulture.cloud.iot_client import CloudMqttManager, MockMqttTransport
from sericulture.cloud.outbox_sync import OutboxSyncWorker
from sericulture.config import CloudConfig, StorageConfig
from sericulture.event_types import SensorHealth, SHT40Reading
from sericulture.storage.database import DatabaseManager
from sericulture.storage.event_repository import EventRepository


@pytest.fixture
def sync_setup(tmp_path):
    db_file = tmp_path / "sync_test.db"
    db_cfg = StorageConfig(db_path=str(db_file), min_free_disk_mb=50)
    db = DatabaseManager(db_cfg)
    db.initialize()
    repo = EventRepository(db, device_id="TEST-DEV-01", farm_id="TEST-FARM-01")

    cloud_cfg = CloudConfig(
        enabled=True,
        endpoint="mock.iot.aws.com",
        client_id="TEST-DEV-01",
        batch_size=5,
    )
    mock_transport = MockMqttTransport()
    mqtt_mgr = CloudMqttManager(cloud_cfg, device_id="TEST-DEV-01", transport=mock_transport)
    worker = OutboxSyncWorker(cloud_cfg, repository=repo, mqtt_manager=mqtt_mgr)

    return repo, mqtt_mgr, mock_transport, worker


def test_outbox_batch_sync_and_durable_ack(sync_setup):
    repo, mqtt_mgr, mock_transport, worker = sync_setup

    # Insert a telemetry event
    reading = SHT40Reading(temperature_c=28.0, relative_humidity_pct=72.0, health=SensorHealth.VALID)
    event_id = repo.record_telemetry(
        reading=reading,
        fan_on=False,
        mist_on=False,
        light_on=False,
        float_state="WATER_OK",
        mains_present=True,
        camera_ok=True,
        hailo_ok=True,
    )

    # Sync batch
    sent_count = worker.sync_batch(limit=10)
    assert sent_count == 1
    assert len(mock_transport.published_messages) == 1

    # Verify event is in flight
    counts = repo.get_outbox_counts()
    assert counts["in_flight"] == 1
    assert counts["acked"] == 0

    # Simulate cloud sending durable application ACK
    ack_payload = json.dumps({
        "event_id": event_id,
        "status": "DURABLY_STORED",
        "timestamp_utc": "2026-10-10T10:00:00Z",
    })
    mock_transport.simulate_incoming_message(
        topic="novabion/sericulture/v1/devices/TEST-DEV-01/acks",
        payload=ack_payload,
    )

    # Verify state transitioned to ACKED
    counts_after = repo.get_outbox_counts()
    assert counts_after["acked"] == 1
    assert counts_after["in_flight"] == 0
