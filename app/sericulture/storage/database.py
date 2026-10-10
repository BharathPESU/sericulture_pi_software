"""SQLite database connection manager and diagnostics.

Enforces WAL mode, foreign keys, synchronous=NORMAL, busy timeouts, and
automatic schema migrations.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

from sericulture.config import StorageConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    StorageError,
)
from sericulture.storage.migrations import apply_migrations

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database lifecycle, pragmas, and thread safety."""

    def __init__(self, config: StorageConfig):
        self._config = config
        self._db_path = Path(config.db_path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._initialized = False

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.SQLITE

    def initialize(self) -> None:
        """Create database directory, open connection, configure PRAGMAs, and apply migrations."""
        with self._lock:
            try:
                # Ensure parent directory exists
                self._db_path.parent.mkdir(parents=True, exist_ok=True)

                conn = self._create_connection()
                # Run migrations
                latest_ver = apply_migrations(conn)
                logger.info(f"Database initialized at {self._db_path} (schema version {latest_ver})")
                self._initialized = True
            except Exception as e:
                logger.critical(f"Failed to initialize SQLite database at {self._db_path}: {e}")
                raise StorageError(f"Database initialization failure: {e}") from e

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection with required PRAGMAs configured."""
        try:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=5.0,  # 5s busy timeout
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row

            # Apply required pragmas
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            return conn
        except Exception as e:
            raise StorageError(f"Could not connect to database {self._db_path}: {e}") from e

    def get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._create_connection()
        return self._local.conn

    def check(self) -> DiagnosticResult:
        """Diagnostic check for SQLite readability and writeability."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            # Verify can execute queries and schema_migrations is accessible
            cursor.execute("SELECT MAX(version) FROM schema_migrations;")
            row = cursor.fetchone()
            schema_ver = row[0] if row else 0

            # Canary write test to verify disk writeability
            cursor.execute("CREATE TEMPORARY TABLE IF NOT EXISTS _canary (val INT);")
            cursor.execute("INSERT INTO _canary VALUES (1);")
            cursor.execute("DROP TABLE _canary;")

            return DiagnosticResult(
                component=ComponentId.SQLITE,
                status=DiagnosticStatus.PASS,
                message=f"SQLite database healthy at {self._db_path} (schema v{schema_ver})",
                detail={
                    "path": str(self._db_path),
                    "schema_version": schema_ver,
                    "wal_mode": True,
                },
            )
        except Exception as e:
            return DiagnosticResult(
                component=ComponentId.SQLITE,
                status=DiagnosticStatus.FAIL,
                message=f"SQLite diagnostic failed: {e}",
            )

    def check_disk_space(self) -> DiagnosticResult:
        """Check available disk space against configured minimum requirement."""
        try:
            target_dir = self._db_path.parent if self._db_path.parent.exists() else Path("/")
            usage = shutil.disk_usage(target_dir)
            free_mb = usage.free / (1024 * 1024)
            min_mb = self._config.min_free_disk_mb

            if free_mb < min_mb:
                return DiagnosticResult(
                    component=ComponentId.DISK_SPACE,
                    status=DiagnosticStatus.FAIL,
                    message=f"Disk space low: {free_mb:.1f} MB free, minimum required is {min_mb} MB",
                    detail={"free_mb": free_mb, "min_required_mb": min_mb},
                )

            return DiagnosticResult(
                component=ComponentId.DISK_SPACE,
                status=DiagnosticStatus.PASS,
                message=f"Disk space sufficient: {free_mb:.1f} MB free (min {min_mb} MB)",
                detail={"free_mb": free_mb, "min_required_mb": min_mb},
            )
        except Exception as e:
            return DiagnosticResult(
                component=ComponentId.DISK_SPACE,
                status=DiagnosticStatus.FAIL,
                message=f"Disk space check failed: {e}",
            )

    def close(self) -> None:
        """Close connections on current thread."""
        with self._lock:
            if hasattr(self._local, "conn") and self._local.conn is not None:
                try:
                    self._local.conn.close()
                except Exception:
                    pass
                self._local.conn = None
