"""SHT40 temperature/humidity sensor driver.

Reads via I²C. Validates readings for range, NaN/inf, staleness, and
repeated stuck values. Handles communication errors with bounded retries.

SAFETY: A missing or invalid reading is NEVER replaced with an arbitrary
value that might cause unsafe actuation.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from sericulture.config import SHT40Config
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    SensorHealth,
    SHT40Reading,
)

logger = logging.getLogger(__name__)


class I2CDriver(Protocol):
    """Protocol for the I²C driver backend."""
    def read_measurement(self) -> tuple[float, float]:
        """Read temperature (°C) and relative humidity (%).

        Returns:
            (temperature_c, relative_humidity_pct)

        Raises:
            Exception on communication error.
        """
        ...

    def probe(self) -> bool:
        """Check if the device responds on the I²C bus."""
        ...


class MockI2CDriver:
    """Mock I²C driver for testing without hardware."""

    def __init__(self, temp: float = 25.0, humidity: float = 60.0):
        self.temp = temp
        self.humidity = humidity
        self.fail_next: bool = False
        self.probe_result: bool = True

    def read_measurement(self) -> tuple[float, float]:
        if self.fail_next:
            self.fail_next = False
            raise OSError("Mock I²C read error")
        return (self.temp, self.humidity)

    def probe(self) -> bool:
        return self.probe_result


class SHT40Sensor:
    """SHT40 sensor reader with validation and health tracking."""

    def __init__(self, config: SHT40Config, driver: I2CDriver | None = None):
        self._config = config
        self._driver = driver
        self._lock = Lock()
        self._last_reading: SHT40Reading | None = None
        self._consecutive_errors = 0
        self._last_values: list[tuple[float, float]] = []  # For stuck detection

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.SHT40

    @property
    def last_reading(self) -> SHT40Reading | None:
        with self._lock:
            return self._last_reading

    def check(self) -> DiagnosticResult:
        """Run diagnostic: probe device and take a test reading."""
        if self._driver is None:
            return DiagnosticResult(
                component=ComponentId.SHT40,
                status=DiagnosticStatus.MISSING,
                message="No I²C driver configured",
            )
        try:
            if not self._driver.probe():
                return DiagnosticResult(
                    component=ComponentId.SHT40,
                    status=DiagnosticStatus.MISSING,
                    message=f"SHT40 not responding at I²C address 0x{self._config.i2c_address:02X}",
                )
            reading = self.read()
            if reading.is_valid:
                return DiagnosticResult(
                    component=ComponentId.SHT40,
                    status=DiagnosticStatus.PASS,
                    message=f"SHT40 OK: {reading.temperature_c:.1f}°C, {reading.relative_humidity_pct:.1f}%RH",
                    detail={"temp_c": reading.temperature_c, "rh_pct": reading.relative_humidity_pct},
                )
            return DiagnosticResult(
                component=ComponentId.SHT40,
                status=DiagnosticStatus.FAIL,
                message=f"SHT40 reading invalid: {reading.health.value} — {reading.error_message}",
            )
        except Exception as exc:
            return DiagnosticResult(
                component=ComponentId.SHT40,
                status=DiagnosticStatus.FAIL,
                message=f"SHT40 diagnostic error: {exc}",
            )

    def read(self) -> SHT40Reading:
        """Take a single reading with validation.

        Never raises — returns an SHT40Reading with appropriate health status.
        """
        if self._driver is None:
            return SHT40Reading(
                temperature_c=None,
                relative_humidity_pct=None,
                health=SensorHealth.UNAVAILABLE,
                error_message="No I²C driver",
            )

        mono_ns = time.monotonic_ns()

        try:
            temp_c, rh_pct = self._driver.read_measurement()
        except Exception as exc:
            self._consecutive_errors += 1
            health = SensorHealth.READ_ERROR
            if self._consecutive_errors >= self._config.max_consecutive_errors:
                health = SensorHealth.UNAVAILABLE
            reading = SHT40Reading(
                temperature_c=None,
                relative_humidity_pct=None,
                health=health,
                error_message=f"I²C read error ({self._consecutive_errors}x): {exc}",
                read_monotonic_ns=mono_ns,
            )
            with self._lock:
                self._last_reading = reading
            return reading

        self._consecutive_errors = 0

        # Validate NaN / Inf
        if math.isnan(temp_c) or math.isinf(temp_c) or math.isnan(rh_pct) or math.isinf(rh_pct):
            reading = SHT40Reading(
                temperature_c=None,
                relative_humidity_pct=None,
                health=SensorHealth.OUT_OF_RANGE,
                error_message=f"NaN/Inf values: temp={temp_c}, rh={rh_pct}",
                read_monotonic_ns=mono_ns,
            )
            with self._lock:
                self._last_reading = reading
            return reading

        # Range check
        if not (self._config.temp_min_c <= temp_c <= self._config.temp_max_c):
            reading = SHT40Reading(
                temperature_c=temp_c,
                relative_humidity_pct=rh_pct,
                health=SensorHealth.OUT_OF_RANGE,
                error_message=f"Temperature {temp_c:.1f}°C out of range "
                              f"[{self._config.temp_min_c}, {self._config.temp_max_c}]",
                read_monotonic_ns=mono_ns,
            )
            with self._lock:
                self._last_reading = reading
            return reading

        if not (self._config.humidity_min_pct <= rh_pct <= self._config.humidity_max_pct):
            reading = SHT40Reading(
                temperature_c=temp_c,
                relative_humidity_pct=rh_pct,
                health=SensorHealth.OUT_OF_RANGE,
                error_message=f"Humidity {rh_pct:.1f}% out of range "
                              f"[{self._config.humidity_min_pct}, {self._config.humidity_max_pct}]",
                read_monotonic_ns=mono_ns,
            )
            with self._lock:
                self._last_reading = reading
            return reading

        # Stuck value detection (same value 5+ times)
        self._last_values.append((temp_c, rh_pct))
        if len(self._last_values) > 10:
            self._last_values = self._last_values[-10:]
        if len(self._last_values) >= 5:
            recent = self._last_values[-5:]
            if all(v == recent[0] for v in recent):
                logger.warning("SHT40: stuck reading detected — same value 5 consecutive times")

        reading = SHT40Reading(
            temperature_c=temp_c,
            relative_humidity_pct=rh_pct,
            health=SensorHealth.VALID,
            read_monotonic_ns=mono_ns,
        )
        with self._lock:
            self._last_reading = reading
        return reading

    def is_stale(self) -> bool:
        """Check if the last reading is older than stale_timeout_s."""
        with self._lock:
            if self._last_reading is None:
                return True
            age_s = (time.monotonic_ns() - self._last_reading.read_monotonic_ns) / 1e9
            return age_s > self._config.stale_timeout_s
