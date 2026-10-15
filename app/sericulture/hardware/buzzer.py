"""Non-blocking active buzzer controller with configurable beep patterns.

DESIGN:
  - Uses a dedicated daemon thread so beep patterns never block the main
    safety loop, sensor reads, or relay control.
  - Patterns are queued with priority (critical faults first).
  - Rate-limits repeated alerts to avoid continuous buzzing.
  - Fails gracefully — a buzzer fault never crashes the supervisor.

HARDWARE NOTE:
  An active buzzer produces ONE pitch. Do not promise multiple tones.
  Writing GPIO HIGH = buzzer sounds, GPIO LOW = silent.
  Actual sound output is UNVERIFIABLE without an external microphone.
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from sericulture.config import BuzzerConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    FaultCategory,
    HardwareError,
)

logger = logging.getLogger(__name__)


class BuzzerPriority(IntEnum):
    """Priority levels — lower number = higher priority."""
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


# Map fault categories to priorities
_FAULT_PRIORITY: dict[FaultCategory, BuzzerPriority] = {
    FaultCategory.SHT40_FAULT:        BuzzerPriority.CRITICAL,
    FaultCategory.CAMERA_FAULT:       BuzzerPriority.CRITICAL,
    FaultCategory.HAILO_FAULT:        BuzzerPriority.HIGH,
    FaultCategory.SAFETY_INPUT_FAULT: BuzzerPriority.CRITICAL,
    FaultCategory.STORAGE_FAULT:      BuzzerPriority.HIGH,
    FaultCategory.CONFIG_GPIO_FAULT:  BuzzerPriority.CRITICAL,
    FaultCategory.CLOUD_DISCONNECTED: BuzzerPriority.LOW,
}


@dataclass
class BuzzerRequest:
    """A request to play a beep pattern."""
    fault: FaultCategory
    priority: BuzzerPriority
    pattern: list[tuple[float, float]]   # [(on_s, off_s), ...]
    repeat: bool = True
    max_repeats: int = 3
    timestamp: float = 0.0              # monotonic time when enqueued

    def __lt__(self, other: BuzzerRequest) -> bool:
        return self.priority < other.priority


class BuzzerGPIO:
    """Protocol-compatible GPIO wrapper for the buzzer pin."""

    def __init__(self, bcm_pin: int, gpio_factory: Any = None):
        self._bcm_pin = bcm_pin
        self._factory = gpio_factory
        self._device: Any = None

    def initialize(self) -> None:
        if self._factory is not None:
            self._device = self._factory.create_output(self._bcm_pin, initial_value=False)
        else:
            from gpiozero import DigitalOutputDevice  # type: ignore
            self._device = DigitalOutputDevice(
                self._bcm_pin, active_high=True, initial_value=False
            )

    def on(self) -> None:
        if self._device:
            self._device.on()

    def off(self) -> None:
        if self._device:
            self._device.off()

    def close(self) -> None:
        if self._device:
            try:
                self._device.off()
                self._device.close()
            except Exception:
                pass
            self._device = None


class BuzzerController:
    """Non-blocking buzzer controller with pattern playback.

    Usage:
        buzzer = BuzzerController(config)
        buzzer.start()
        buzzer.play_fault(FaultCategory.SHT40_FAULT)
        ...
        buzzer.stop()
    """

    def __init__(
        self,
        config: BuzzerConfig,
        gpio_factory: Any = None,
    ):
        self._config = config
        self._gpio = BuzzerGPIO(config.bcm_gpio, gpio_factory)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._request_event = threading.Event()
        self._lock = threading.Lock()
        self._current_request: BuzzerRequest | None = None
        self._last_play_times: dict[FaultCategory, float] = {}
        self._initialized = False
        self._enabled = config.enabled

    def initialize(self) -> DiagnosticResult:
        """Initialize the buzzer GPIO pin."""
        if not self._enabled:
            return DiagnosticResult(
                component=ComponentId.BUZZER,
                status=DiagnosticStatus.OPTIONAL_MISSING,
                message="Buzzer disabled in configuration",
            )
        try:
            self._gpio.initialize()
            self._initialized = True
            # We CANNOT verify the buzzer made sound — only that GPIO was set
            return DiagnosticResult(
                component=ComponentId.BUZZER,
                status=DiagnosticStatus.UNVERIFIABLE,
                message="Buzzer GPIO initialized (COMMAND_SENT/UNVERIFIABLE — "
                        "no audio feedback to confirm sound output)",
            )
        except Exception as exc:
            logger.error("Buzzer GPIO init failed: %s", exc)
            return DiagnosticResult(
                component=ComponentId.BUZZER,
                status=DiagnosticStatus.FAIL,
                message=f"Buzzer GPIO init failed: {exc}",
            )

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.BUZZER

    def check(self) -> DiagnosticResult:
        return self.initialize() if not self._initialized else DiagnosticResult(
            component=ComponentId.BUZZER,
            status=DiagnosticStatus.UNVERIFIABLE,
            message="Buzzer GPIO available (sound output UNVERIFIABLE)",
        )

    def start(self) -> None:
        """Start the buzzer worker thread."""
        if not self._enabled or not self._initialized:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="buzzer-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info("Buzzer worker thread started")

    def stop(self) -> None:
        """Stop the buzzer worker and silence the buzzer."""
        self._stop_event.set()
        self._request_event.set()  # Wake the worker
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._gpio.off()
        self._gpio.close()
        logger.info("Buzzer worker stopped")

    def play_fault(self, fault: FaultCategory) -> bool:
        """Queue a fault beep pattern.

        Returns True if the pattern was accepted, False if rate-limited or disabled.
        """
        if not self._enabled or not self._initialized:
            return False

        # Rate limit
        now = time.monotonic()
        last = self._last_play_times.get(fault, 0.0)
        if (now - last) < self._config.max_repeat_interval_s:
            logger.debug("Buzzer: rate-limited %s (%.1fs since last)", fault.value, now - last)
            return False

        pattern = self._config.patterns.get(fault.value)
        if not pattern:
            logger.warning("No buzzer pattern configured for %s", fault.value)
            return False

        priority = _FAULT_PRIORITY.get(fault, BuzzerPriority.MEDIUM)
        request = BuzzerRequest(
            fault=fault,
            priority=priority,
            pattern=pattern,
            repeat=True,
            max_repeats=3,
            timestamp=now,
        )

        with self._lock:
            # Only replace current request if higher priority
            if (self._current_request is None or
                    request.priority <= self._current_request.priority):
                self._current_request = request
                self._last_play_times[fault] = now
                self._request_event.set()
                logger.info("Buzzer: queued %s (priority=%d)", fault.value, priority)
                return True

        return False

    def silence(self) -> None:
        """Immediately stop any playing pattern."""
        with self._lock:
            self._current_request = None
        self._gpio.off()

    def _worker_loop(self) -> None:
        """Background thread: plays patterns without blocking the main loop."""
        while not self._stop_event.is_set():
            # Wait for a request or stop
            self._request_event.wait(timeout=1.0)
            self._request_event.clear()

            if self._stop_event.is_set():
                break

            with self._lock:
                request = self._current_request

            if request is None:
                continue

            try:
                self._play_pattern(request)
            except Exception:
                logger.exception("Buzzer pattern playback error")
                try:
                    self._gpio.off()
                except Exception:
                    pass

            with self._lock:
                if self._current_request is request:
                    self._current_request = None

        # Ensure buzzer is off when exiting
        try:
            self._gpio.off()
        except Exception:
            pass

    def _play_pattern(self, request: BuzzerRequest) -> None:
        """Play a beep pattern with optional repeats."""
        for repeat_num in range(request.max_repeats if request.repeat else 1):
            if self._stop_event.is_set():
                return

            for on_s, off_s in request.pattern:
                if self._stop_event.is_set():
                    return
                # Check if a higher-priority request preempted us
                with self._lock:
                    if (self._current_request is not None and
                            self._current_request is not request):
                        return

                self._gpio.on()
                # Sleep in small increments to stay responsive
                self._interruptible_sleep(on_s)
                self._gpio.off()
                self._interruptible_sleep(off_s)

            # Pause between repeats
            if repeat_num < (request.max_repeats - 1):
                self._interruptible_sleep(1.5)

    def _interruptible_sleep(self, duration: float) -> None:
        """Sleep that can be interrupted by stop_event."""
        end_time = time.monotonic() + duration
        while time.monotonic() < end_time:
            if self._stop_event.is_set():
                return
            remaining = end_time - time.monotonic()
            time.sleep(min(0.05, max(0.0, remaining)))
