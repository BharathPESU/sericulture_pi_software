"""Unit tests for SQLite storage, migrations, and transactional outbox."""

import pytest
import sqlite3
from sericulture.config import StorageConfig
from sericulture.event_types import EventType, SensorHealth, SHT40Reading
from sericulture.storage.database import DatabaseManager
from sericulture.storage.event_repository import EventRepository


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test.db"
    cfg = StorageConfig(db_path=str(db_file), min_free_disk_mb=50)
    db = DatabaseManager(cfg)
    db.initialize()
    repository = EventRepository(db, device_id="TEST-DEV-01", farm_id="TEST-FARM-01")
    return repository, db


def test_atomic_telemetry_outbox_insertion(repo):
    repository, db = repo
    reading = SHT40Reading(temperature_c=27.2, relative_humidity_pct=81.4, health=SensorHealth.VALID)

    event_id = repository.record_telemetry(
        reading=reading,
        fan_on=False,
        mist_on=True,
        light_on=False,
        float_state="WATER_OK",
        mains_present=True,
        camera_ok=True,
        hailo_ok=True,
    )

    # Verify present in telemetry table
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM telemetry WHERE event_id = ?", (event_id,)).fetchone()
    assert row is not None
    assert row["temperature_c"] == 27.2

    # Verify present in outbox table with status PENDING
    outbox_batch = repository.get_pending_outbox_batch(limit=10)
    assert len(outbox_batch) == 1
    assert outbox_batch[0].event_id == event_id
    assert outbox_batch[0].sync_status == "PENDING"


def test_outbox_state_lifecycle(repo):
    repository, db = repo
    reading = SHT40Reading(temperature_c=25.0, relative_humidity_pct=70.0, health=SensorHealth.VALID)
    event_id = repository.record_telemetry(
        reading=reading,
        fan_on=False,
        mist_on=False,
        light_on=False,
        float_state="WATER_OK",
        mains_present=True,
        camera_ok=True,
        hailo_ok=True,
    )

    # 1. Mark IN_FLIGHT
    repository.mark_outbox_in_flight([event_id])
    counts = repository.get_outbox_counts()
    assert counts["in_flight"] == 1
    assert counts["pending"] == 0

    # 2. Mark ACKED
    repository.mark_outbox_acked([event_id])
    counts2 = repository.get_outbox_counts()
    assert counts2["acked"] == 1
    assert counts2["in_flight"] == 0


def test_cleanup_never_deletes_unsynced_events(repo):
    repository, db = repo
    # Insert pending event
    reading = SHT40Reading(temperature_c=25.0, relative_humidity_pct=70.0, health=SensorHealth.VALID)
    repository.record_telemetry(
        reading=reading,
        fan_on=False,
        mist_on=False,
        light_on=False,
        float_state="WATER_OK",
        mains_present=True,
        camera_ok=True,
        hailo_ok=True,
    )

    # Cleanup with 0 days
    repository.cleanup_acked_events(retention_days=0)
    # Pending must remain untouched
    batch = repository.get_pending_outbox_batch(limit=10)
    assert len(batch) == 1
