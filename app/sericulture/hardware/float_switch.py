"""Magnetic float level switch monitor.

Monitors water level for mist sprayer safety. Validates contact state
(NO/NC configuration) with software debounce.

SAFETY RULES:
  - If float switch indicates WATER_LOW, misting is strictly disallowed.
  - If float switch is UNKNOWN or FAULT, misting is disallowed.
  - Software cannot detect wire breaks on simple 2-wire NO/NC circuits;
    this limitation is documented and not hidden.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Protocol

from sericulture.config import FloatSwitchConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    FloatSwitchContactType,
    FloatSwitchState,
)

logger = logging.getLogger(__name__)


class DigitalInputDriver(Protocol):
    """Protocol for reading a digital GPIO pin."""
    def read_pin(self) -> int:
        """Read pin raw logic level (0 = LOW, 1 = HIGH)."""
        ...

    def close(self) -> None:
        """Release GPIO resources."""
        ...


class MockDigitalInputDriver:
    """Mock digital input driver for simulation/tests."""

    def __init__(self, initial_level: int = 0):
        self.level: int = initial_level
        self.is_closed: bool = False

    def read_pin(self) -> int:
        return self.level

    def close(self) -> None:
        self.is_closed = True


class FloatSwitchMonitor:
    """Monitors magnetic float level switch with debounce and NO/NC polarity."""

    def __init__(
        self,
        config: FloatSwitchConfig,
        driver: DigitalInputDriver | None = None,
    ):
        self._config = config
        self._driver = driver or MockDigitalInputDriver()
        self._lock = Lock()
        self._last_raw_value: int | None = None
        self._last_change_time: float = 0.0
        self._debounced_state: FloatSwitchState = FloatSwitchState.UNKNOWN
        self._debounce_sec: float = config.debounce_ms / 1000.0

        # Prime initial reading
        self._update_state()

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.FLOAT_SWITCH

    def _raw_to_state(self, raw_level: int) -> FloatSwitchState:
        """Translate raw GPIO logic level to FloatSwitchState based on contact configuration.

        Typically, a switch wired between GPIO and GND with internal pull-up:
          - Switch closed: pin reads 0 (LOW)
          - Switch open: pin reads 1 (HIGH)
        """
        # Determine if switch contact is closed
        # When pull_up is active: LOW (0) means contact is closed to ground.
        # When pull_down is active: HIGH (1) means contact is closed to VCC.
        is_contact_closed = (raw_level == 0) if self._config.pull_up else (raw_level == 1)

        if self._config.contact_type == FloatSwitchContactType.NORMALLY_OPEN:
            # NO: typically float UP = closed (water ok), float DOWN = open (water low)
            water_ok = is_contact_closed if self._config.water_ok_when_closed else not is_contact_closed
        else:
            # NC: inverted
            water_ok = (not is_contact_closed) if self._config.water_ok_when_closed else is_contact_closed

        return FloatSwitchState.WATER_OK if water_ok else FloatSwitchState.WATER_LOW

    def _update_state(self) -> FloatSwitchState:
        with self._lock:
            try:
                raw = self._driver.read_pin()
            except Exception as e:
                logger.error(f"Failed to read float switch GPIO: {e}")
                self._debounced_state = FloatSwitchState.FAULT
                return self._debounced_state

            now = time.monotonic()
            if self._last_raw_value is None:
                self._last_raw_value = raw
                self._last_change_time = now
                self._debounced_state = self._raw_to_state(raw)
            elif raw != self._last_raw_value:
                self._last_raw_value = raw
                self._last_change_time = now
            elif (now - self._last_change_time) >= self._debounce_sec:
                self._debounced_state = self._raw_to_state(raw)

            return self._debounced_state

    def get_state(self) -> FloatSwitchState:
        """Get current debounced float switch state."""
        return self._update_state()

    def is_water_level_ok(self) -> bool:
        """Returns True ONLY if water level is positively confirmed OK."""
        return self.get_state() == FloatSwitchState.WATER_OK

    def check(self) -> DiagnosticResult:
        """Diagnostic check for float switch input."""
        try:
            raw = self._driver.read_pin()
            state = self._raw_to_state(raw)
            return DiagnosticResult(
                component=ComponentId.FLOAT_SWITCH,
                status=DiagnosticStatus.PASS,
                message=f"Float switch GPIO read successfully. State: {state.value} (raw={raw})",
                detail={
                    "raw_pin_level": raw,
                    "state": state.value,
                    "contact_type": self._config.contact_type.value,
                    "pull_up": self._config.pull_up,
                    "limitation": "2-wire circuit: cannot distinguish open switch from wire break",
                },
            )
        except Exception as e:
            return DiagnosticResult(
                component=ComponentId.FLOAT_SWITCH,
                status=DiagnosticStatus.FAIL,
                message=f"Float switch GPIO read error: {e}",
            )

    def close(self) -> None:
        """Clean up GPIO resources."""
        with self._lock:
            if hasattr(self._driver, "close"):
                self._driver.close()
