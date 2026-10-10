"""Isolated mains-presence monitor.

Monitors the safe low-voltage logic output of the isolated AC mains detector.
Applies software debounce, tracks outage duration, and requires stabilization
before considering mains restored.

SAFETY RULES:
  - When mains is LOST, all load actuators must be commanded OFF immediately.
  - When mains returns, automatic loads MUST NOT restart immediately.
    They require debounce stabilization and recovery policy approval.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Callable, Protocol

from sericulture.config import MainsDetectorConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    MainsState,
)

logger = logging.getLogger(__name__)


class MainsInputDriver(Protocol):
    """Protocol for reading mains detector GPIO logic level."""
    def read_pin(self) -> int:
        ...

    def close(self) -> None:
        ...


class MockMainsInputDriver:
    """Mock mains detector driver for simulation and tests."""

    def __init__(self, initial_level: int = 1):
        self.level: int = initial_level
        self.is_closed: bool = False

    def read_pin(self) -> int:
        return self.level

    def close(self) -> None:
        self.is_closed = True


class MainsMonitor:
    """Isolated AC mains detector monitor with debounce and recovery stabilization."""

    def __init__(
        self,
        config: MainsDetectorConfig,
        driver: MainsInputDriver | None = None,
        recovery_stabilize_sec: float = 3.0,
    ):
        self._config = config
        self._driver = driver or MockMainsInputDriver()
        self._recovery_stabilize_sec = recovery_stabilize_sec
        self._debounce_sec = config.debounce_ms / 1000.0

        self._lock = Lock()
        self._state: MainsState = MainsState.UNKNOWN
        self._last_raw: int | None = None
        self._raw_change_time: float = 0.0

        self._outage_start_monotonic: float | None = None
        self._restoration_candidate_time: float | None = None

        self._on_lost_callbacks: list[Callable[[], None]] = []
        self._on_restored_callbacks: list[Callable[[float], None]] = []  # arg: outage_duration_sec

        # Initialize state
        self._update()

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.MAINS_DETECTOR

    def add_on_lost_listener(self, callback: Callable[[], None]) -> None:
        """Register callback for when mains loss is confirmed."""
        with self._lock:
            self._on_lost_callbacks.append(callback)

    def add_on_restored_listener(self, callback: Callable[[float], None]) -> None:
        """Register callback for when mains restoration is stabilized (receives duration in s)."""
        with self._lock:
            self._on_restored_callbacks.append(callback)

    def _is_present_raw(self, raw_level: int) -> bool:
        return (raw_level == 1) if self._config.active_high else (raw_level == 0)

    def _update(self) -> MainsState:
        with self._lock:
            try:
                raw = self._driver.read_pin()
            except Exception as e:
                logger.error(f"Error reading mains detector GPIO: {e}")
                self._state = MainsState.UNKNOWN
                return self._state

            now = time.monotonic()
            if self._last_raw is None:
                self._last_raw = raw
                self._raw_change_time = now
                self._state = MainsState.PRESENT if self._is_present_raw(raw) else MainsState.LOST
                if self._state == MainsState.LOST:
                    self._outage_start_monotonic = now
                return self._state

            if raw != self._last_raw:
                self._last_raw = raw
                self._raw_change_time = now
                return self._state

            # If raw value has been stable for at least debounce interval
            if (now - self._raw_change_time) >= self._debounce_sec:
                raw_present = self._is_present_raw(raw)

                if self._state == MainsState.PRESENT and not raw_present:
                    # Mains Lost confirmed
                    self._state = MainsState.LOST
                    self._outage_start_monotonic = now
                    self._restoration_candidate_time = None
                    logger.warning("MAINS POWER LOST detected by isolated detector.")
                    callbacks = list(self._on_lost_callbacks)
                    # Trigger callbacks outside lock
                    # We'll trigger them after releasing lock or copy
                    for cb in callbacks:
                        try:
                            cb()
                        except Exception as cb_err:
                            logger.error(f"Error in mains-lost callback: {cb_err}")

                elif self._state == MainsState.LOST and raw_present:
                    # Mains returned, but needs stabilization
                    if self._restoration_candidate_time is None:
                        self._restoration_candidate_time = now
                        logger.info("Mains signal detected, waiting for stabilization...")
                    elif (now - self._restoration_candidate_time) >= self._recovery_stabilize_sec:
                        # Fully stabilized
                        self._state = MainsState.PRESENT
                        outage_dur = 0.0
                        if self._outage_start_monotonic:
                            outage_dur = now - self._outage_start_monotonic
                        self._outage_start_monotonic = None
                        self._restoration_candidate_time = None
                        logger.info(f"MAINS POWER RESTORED after {outage_dur:.1f}s stabilization.")
                        callbacks = list(self._on_restored_callbacks)
                        for cb in callbacks:
                            try:
                                cb(outage_dur)
                            except Exception as cb_err:
                                logger.error(f"Error in mains-restored callback: {cb_err}")

                elif not raw_present:
                    # Still lost
                    self._restoration_candidate_time = None

            return self._state

    def get_state(self) -> MainsState:
        """Get current mains power state."""
        return self._update()

    def is_mains_present(self) -> bool:
        """Returns True if mains is positively present."""
        return self.get_state() == MainsState.PRESENT

    def check(self) -> DiagnosticResult:
        """Run diagnostic check on mains detector."""
        try:
            raw = self._driver.read_pin()
            present = self._is_present_raw(raw)
            state = MainsState.PRESENT if present else MainsState.LOST
            return DiagnosticResult(
                component=ComponentId.MAINS_DETECTOR,
                status=DiagnosticStatus.PASS,
                message=f"Mains detector GPIO read successfully. State: {state.value} (raw={raw})",
                detail={
                    "raw_pin_level": raw,
                    "mains_present": present,
                    "active_high": self._config.active_high,
                    "limitation": "Software observes low-voltage logic output only; circuit must be isolated",
                },
            )
        except Exception as e:
            return DiagnosticResult(
                component=ComponentId.MAINS_DETECTOR,
                status=DiagnosticStatus.FAIL,
                message=f"Mains detector GPIO read error: {e}",
            )

    def close(self) -> None:
        with self._lock:
            if hasattr(self._driver, "close"):
                self._driver.close()
