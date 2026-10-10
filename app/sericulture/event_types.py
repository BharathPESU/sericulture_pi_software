"""Event types, diagnostic statuses, and domain exceptions.

This module defines all enums, dataclasses, and exception types used across
the sericulture system. It has NO internal imports to avoid circular deps.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any


# ──────────────────────────────────────────────────────────────────────
# System States (State Machine)
# ──────────────────────────────────────────────────────────────────────

class SystemState(Enum):
    """All states in the safe-boot state machine."""
    BOOTING          = "BOOTING"
    SAFE_OUTPUTS     = "SAFE_OUTPUTS"
    DIAGNOSTICS      = "DIAGNOSTICS"
    FAULT_LOCKOUT    = "FAULT_LOCKOUT"
    SELF_TEST        = "SELF_TEST"
    MANUAL_READY     = "MANUAL_READY"
    AUTO_READY       = "AUTO_READY"
    MAINS_LOST       = "MAINS_LOST"
    OFFLINE_OPERATION = "OFFLINE_OPERATION"
    SHUTTING_DOWN    = "SHUTTING_DOWN"
    RECOVERY_PENDING = "RECOVERY_PENDING"


# ──────────────────────────────────────────────────────────────────────
# Diagnostic Status
# ──────────────────────────────────────────────────────────────────────

class DiagnosticStatus(Enum):
    """Hardware/component diagnostic status.

    These are semantically distinct — do NOT merge or alias them.
    """
    PASS             = "PASS"               # Tested with observable evidence
    FAIL             = "FAIL"               # Test failed or value invalid
    MISSING          = "MISSING"            # Expected device not present
    UNVERIFIABLE     = "UNVERIFIABLE"       # No feedback hardware to verify
    OPTIONAL_MISSING = "OPTIONAL_MISSING"   # Optional part absent, policy allows
    NOT_CHECKED      = "NOT_CHECKED"        # Not yet tested


class ComponentId(Enum):
    """Identifiers for diagnosable system components."""
    GPIO_BACKEND   = "gpio_backend"
    SHT40          = "sht40"
    CAMERA         = "camera"
    HAILO_DEVICE   = "hailo_device"
    HAILO_MODEL    = "hailo_model"
    FLOAT_SWITCH   = "float_switch"
    MAINS_DETECTOR = "mains_detector"
    BUZZER         = "buzzer"
    RELAY_BOARD    = "relay_board"
    SQLITE         = "sqlite"
    DISK_SPACE     = "disk_space"
    CONFIG         = "config"
    CLOCK          = "clock"
    AWS_IOT        = "aws_iot"
    DISPLAY        = "display"


@dataclass(frozen=True)
class DiagnosticResult:
    """Result of a single component diagnostic check."""
    component: ComponentId
    status: DiagnosticStatus
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_healthy(self) -> bool:
        return self.status in (
            DiagnosticStatus.PASS,
            DiagnosticStatus.OPTIONAL_MISSING,
            DiagnosticStatus.UNVERIFIABLE,
        )


# ──────────────────────────────────────────────────────────────────────
# Relay / Actuator Types
# ──────────────────────────────────────────────────────────────────────

class RelayPolarity(Enum):
    """Whether a relay channel is triggered by HIGH or LOW GPIO output.

    MUST be validated against the actual relay board at commissioning.
    """
    ACTIVE_HIGH = "active_high"   # GPIO HIGH = relay energized/ON
    ACTIVE_LOW  = "active_low"    # GPIO LOW  = relay energized/ON


class LoadId(Enum):
    """Logical load identifiers for the safety layer."""
    FAN        = "fan"
    MIST       = "mist"
    NIGHT_LIGHT = "night_light"
    SPARE_CH4  = "spare_ch4"
    SPARE_CH5  = "spare_ch5"
    SPARE_CH6  = "spare_ch6"


class ActuatorCommand(Enum):
    """Commanded relay state."""
    OFF = "OFF"
    ON  = "ON"


@dataclass(frozen=True)
class ActuatorRequest:
    """A request to change a load's state, passed through the safety layer."""
    load: LoadId
    desired: ActuatorCommand
    reason: str
    source: str                                     # e.g. "decision_engine", "mains_monitor", "manual"
    timestamp_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class ActuatorResult:
    """Result of processing an ActuatorRequest through the safety layer."""
    request: ActuatorRequest
    allowed: bool
    actual_command: ActuatorCommand
    rejection_reason: str = ""
    relay_feedback: str = "UNVERIFIABLE"           # UNVERIFIABLE | CONFIRMED | MISMATCH
    timestamp_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ──────────────────────────────────────────────────────────────────────
# Sensor Types
# ──────────────────────────────────────────────────────────────────────

class SensorHealth(Enum):
    """Health status of a sensor reading."""
    VALID         = "VALID"
    STALE         = "STALE"
    OUT_OF_RANGE  = "OUT_OF_RANGE"
    READ_ERROR    = "READ_ERROR"
    UNAVAILABLE   = "UNAVAILABLE"
    NOT_READY     = "NOT_READY"


@dataclass(frozen=True)
class SHT40Reading:
    """A single SHT40 temperature/humidity reading with validity."""
    temperature_c: float | None
    relative_humidity_pct: float | None
    health: SensorHealth
    error_message: str = ""
    read_monotonic_ns: int = 0     # time.monotonic_ns() at read
    timestamp_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_valid(self) -> bool:
        return self.health == SensorHealth.VALID


class FloatSwitchContactType(Enum):
    """Physical contact type of the magnetic float switch."""
    NORMALLY_OPEN  = "NO"     # Open when float is down (low water)
    NORMALLY_CLOSED = "NC"    # Closed when float is down (low water)


class FloatSwitchState(Enum):
    """Logical water level state."""
    WATER_OK        = "WATER_OK"
    WATER_LOW       = "WATER_LOW"
    UNKNOWN         = "UNKNOWN"
    FAULT           = "FAULT"


class MainsState(Enum):
    """Mains power state from the isolated detector."""
    PRESENT     = "PRESENT"
    LOST        = "LOST"
    UNKNOWN     = "UNKNOWN"
    UNSTABLE    = "UNSTABLE"


# ──────────────────────────────────────────────────────────────────────
# Event Types for Storage / Cloud
# ──────────────────────────────────────────────────────────────────────

class EventType(Enum):
    """Categories for locally logged and cloud-synced events."""
    TELEMETRY              = "telemetry"
    BOOT                   = "boot"
    SHUTDOWN               = "shutdown"
    STATE_TRANSITION       = "state_transition"
    ACTUATOR_CHANGE        = "actuator_change"
    MAINS_LOST             = "mains_lost"
    MAINS_RESTORED         = "mains_restored"
    SENSOR_FAULT           = "sensor_fault"
    SENSOR_RECOVERED       = "sensor_recovered"
    CAMERA_FAULT           = "camera_fault"
    CAMERA_RECOVERED       = "camera_recovered"
    HAILO_FAULT            = "hailo_fault"
    HAILO_RECOVERED        = "hailo_recovered"
    FLOAT_SWITCH_CHANGE    = "float_switch_change"
    AI_DETECTION           = "ai_detection"
    INACTIVITY_ALERT       = "inactivity_alert"
    CONNECTIVITY_CHANGE    = "connectivity_change"
    HEALTH_SNAPSHOT        = "health_snapshot"
    STORAGE_FAULT          = "storage_fault"
    SAFETY_INTERLOCK       = "safety_interlock"
    CONFIG_ERROR           = "config_error"
    SOFTWARE_ERROR         = "software_error"
    DIAGNOSTIC_RESULT      = "diagnostic_result"
    MANUAL_OVERRIDE        = "manual_override"
    RELAY_FEEDBACK_MISMATCH = "relay_feedback_mismatch"


class SyncStatus(Enum):
    """Cloud synchronization status for outbox records."""
    PENDING   = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    ACKED     = "ACKED"
    RETRY     = "RETRY"
    FAILED    = "FAILED"       # Permanent failure after max retries


# ──────────────────────────────────────────────────────────────────────
# Fault Categories (for buzzer patterns)
# ──────────────────────────────────────────────────────────────────────

class FaultCategory(Enum):
    """Fault categories mapped to distinct buzzer beep patterns."""
    SHT40_FAULT          = "sht40_fault"
    CAMERA_FAULT         = "camera_fault"
    HAILO_FAULT          = "hailo_fault"
    SAFETY_INPUT_FAULT   = "safety_input_fault"     # float/mains
    STORAGE_FAULT        = "storage_fault"
    CONFIG_GPIO_FAULT    = "config_gpio_fault"
    CLOUD_DISCONNECTED   = "cloud_disconnected"      # low priority


# ──────────────────────────────────────────────────────────────────────
# Domain Exceptions
# ──────────────────────────────────────────────────────────────────────

class SericultureError(Exception):
    """Base exception for all sericulture domain errors."""


class ConfigurationError(SericultureError):
    """Configuration is missing, invalid, or has conflicting values."""


class HardwareError(SericultureError):
    """A hardware component failed or is unavailable."""
    def __init__(self, component: ComponentId, message: str):
        self.component = component
        super().__init__(f"[{component.value}] {message}")


class SafetyViolationError(SericultureError):
    """An operation was blocked by the safety layer."""
    def __init__(self, load: LoadId, reason: str):
        self.load = load
        self.reason = reason
        super().__init__(f"Safety blocked {load.value}: {reason}")


class StorageError(SericultureError):
    """SQLite or local storage operation failed."""


class CloudSyncError(SericultureError):
    """AWS IoT / MQTT communication error."""


class DiagnosticError(SericultureError):
    """A required diagnostic check failed."""
    def __init__(self, result: DiagnosticResult):
        self.result = result
        super().__init__(
            f"Diagnostic FAIL: {result.component.value} — {result.message}"
        )


class BootSupervisorError(SericultureError):
    """Boot supervisor encountered an unrecoverable error."""
