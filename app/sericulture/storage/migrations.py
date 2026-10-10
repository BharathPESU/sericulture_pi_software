"""Database schema migrations for sericulture SQLite storage.

Maintains schema versions, applies migrations sequentially in transactions,
and tracks version in the schema_migrations table.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Callable

logger = logging.getLogger(__name__)

# List of migration functions in order: (version_number, migration_function)
# Each migration runs within its own transaction.

def migration_v1(conn: sqlite3.Connection) -> None:
    """Initial schema version 1: tables for telemetry, events, actuators, AI, health, and outbox."""
    cursor = conn.cursor()

    # Telemetry table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        temperature_c REAL,
        relative_humidity_pct REAL,
        sensor_health TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp_utc);")

    # Device events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS device_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_events_timestamp ON device_events(timestamp_utc);")

    # Actuator events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS actuator_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        load TEXT NOT NULL,
        commanded_state TEXT NOT NULL,
        reason TEXT NOT NULL,
        source TEXT NOT NULL,
        feedback_status TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_actuator_events_timestamp ON actuator_events(timestamp_utc);")

    # AI events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        model_version TEXT NOT NULL,
        tracked_count INTEGER NOT NULL,
        avg_movement_score REAL NOT NULL,
        inactive_count INTEGER NOT NULL,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_events_timestamp ON ai_events(timestamp_utc);")

    # Health snapshots table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS health_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        cpu_usage_pct REAL NOT NULL,
        ram_usage_pct REAL NOT NULL,
        disk_used_pct REAL NOT NULL,
        cpu_temp_c REAL NOT NULL,
        uptime_s REAL NOT NULL,
        service_status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_health_snapshots_timestamp ON health_snapshots(timestamp_utc);")

    # Outbox table for offline persistence and cloud sync
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        farm_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        timestamp_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        sync_status TEXT NOT NULL DEFAULT 'PENDING',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        acked_at TEXT
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(sync_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_created_at ON outbox(created_at);")


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, migration_v1),
]


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Ensure schema_migrations table exists, query current version, and run pending migrations."""
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at_utc TEXT NOT NULL
    );
    """)
    conn.commit()

    cursor.execute("SELECT MAX(version) FROM schema_migrations;")
    row = cursor.fetchone()
    current_version = row[0] if (row and row[0] is not None) else 0

    applied_count = 0
    for version, mig_fn in MIGRATIONS:
        if version > current_version:
            logger.info(f"Applying database migration version {version}...")
            try:
                with conn:  # Transaction context manager
                    mig_fn(conn)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at_utc) VALUES (?, datetime('now'));",
                        (version,),
                    )
                applied_count += 1
                logger.info(f"Successfully applied migration version {version}.")
            except Exception as e:
                logger.critical(f"Database migration version {version} failed: {e}")
                raise

    return current_version + applied_count
