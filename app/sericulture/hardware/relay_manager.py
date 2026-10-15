"""Relay manager — safe GPIO control for the 6-channel relay board.

SAFETY INVARIANTS:
  1. All relay outputs default to OFF at construction.
  2. A single RelayManager instance owns all relay GPIO pins.
  3. Active-high/active-low polarity is read from validated configuration.
  4. A GPIO write does NOT prove that the relay physically switched.
     Without feedback hardware, relay status is UNVERIFIABLE.
  5. All changes go through set_channel() which logs every command.

GPIO BACKEND:
  Uses gpiozero DigitalOutputDevice with lgpio pin factory on Pi 5.
  For testing, accepts a mock factory that records calls without real GPIO.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from sericulture.event_types import (
    ActuatorCommand,
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    HardwareError,
    RelayPolarity,
)
from sericulture.hardware.pin_map import (
    ALL_RELAY_BCM_PINS,
    RelayChannel,
    RelayChannelInfo,
    RELAY_CHANNEL_INFO,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# GPIO abstraction for testing
# ──────────────────────────────────────────────────────────────────────

class GPIOOutputDevice(Protocol):
    """Protocol for a GPIO output device (real or mock)."""
    def on(self) -> None: ...
    def off(self) -> None: ...
    def close(self) -> None: ...
    @property
    def value(self) -> int: ...


class GPIOFactory(Protocol):
    """Protocol for creating GPIO output devices."""
    def create_output(self, bcm_pin: int, initial_value: bool) -> GPIOOutputDevice: ...


class GpiozeroFactory:
    """Real GPIO factory using gpiozero + lgpio for Pi 5."""

    def create_output(self, bcm_pin: int, initial_value: bool) -> GPIOOutputDevice:
        # Import lazily so tests don't need gpiozero installed
        from gpiozero import DigitalOutputDevice  # type: ignore
        return DigitalOutputDevice(
            bcm_pin,
            active_high=True,        # We handle polarity in RelayManager
            initial_value=initial_value,
        )


class MockGPIOOutput:
    """Mock GPIO output for testing — no real hardware."""

    def __init__(self, bcm_pin: int, initial_value: bool):
        self.bcm_pin = bcm_pin
        self._value = 1 if initial_value else 0
        self._closed = False
        self.call_log: list[dict[str, Any]] = []

    def on(self) -> None:
        self._value = 1
        self.call_log.append({"action": "on", "time": time.monotonic()})

    def off(self) -> None:
        self._value = 0
        self.call_log.append({"action": "off", "time": time.monotonic()})

    def close(self) -> None:
        self._closed = True
        self.call_log.append({"action": "close", "time": time.monotonic()})

    @property
    def value(self) -> int:
        return self._value


class MockGPIOFactory:
    """Mock GPIO factory that records all pin operations."""

    def __init__(self):
        self.devices: dict[int, MockGPIOOutput] = {}

    def create_output(self, bcm_pin: int, initial_value: bool) -> MockGPIOOutput:
        dev = MockGPIOOutput(bcm_pin, initial_value)
        self.devices[bcm_pin] = dev
        return dev


# ──────────────────────────────────────────────────────────────────────
# Per-channel state tracking
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ChannelState:
    """Runtime state of one relay channel."""
    channel: RelayChannel
    info: RelayChannelInfo
    polarity: RelayPolarity
    commanded: ActuatorCommand = ActuatorCommand.OFF
    last_change_utc: datetime | None = None
    last_change_mono: float = 0.0
    command_count: int = 0
    last_reason: str = "initialization"

    @property
    def is_on(self) -> bool:
        return self.commanded == ActuatorCommand.ON

    @property
    def seconds_since_change(self) -> float:
        if self.last_change_mono == 0.0:
            return 0.0
        return time.monotonic() - self.last_change_mono


# ──────────────────────────────────────────────────────────────────────
# Relay Manager
# ──────────────────────────────────────────────────────────────────────

class RelayManager:
    """Owns and controls all relay channel GPIOs.

    SAFETY: Exactly ONE RelayManager instance should exist per process.
    All relay GPIO writes go through this class.
    """

    def __init__(
        self,
        channel_polarities: dict[RelayChannel, RelayPolarity] | Any,
        gpio_factory: GPIOFactory | None = None,
    ):
        """Initialize and command all relays to OFF.

        Args:
            channel_polarities: Per-channel polarity dict or RelayConfig object.
            gpio_factory: GPIO backend (default: real gpiozero on Pi 5).

        Raises:
            HardwareError: If GPIO initialization fails.
        """
        self._lock = Lock()
        self._factory = gpio_factory or GpiozeroFactory()
        self._devices: dict[RelayChannel, GPIOOutputDevice] = {}
        self._states: dict[RelayChannel, ChannelState] = {}
        self._initialized = False

        if hasattr(channel_polarities, "channels"):
            # RelayConfig instance passed
            polarities: dict[RelayChannel, RelayPolarity] = {}
            for name, ch_cfg in getattr(channel_polarities, "channels").items():
                for ch in RelayChannel:
                    if ch.value == ch_cfg.bcm_gpio:
                        polarities[ch] = ch_cfg.polarity
                        break
            channel_polarities = polarities

        logger.info("Initializing RelayManager for %d channels", len(channel_polarities))

        for channel, polarity in channel_polarities.items():
            info = RELAY_CHANNEL_INFO[channel]

            # Determine the GPIO level that means "relay OFF"
            # Active-low: OFF = HIGH (pin high = relay de-energized)
            # Active-high: OFF = LOW (pin low = relay de-energized)
            off_level = polarity == RelayPolarity.ACTIVE_LOW

            try:
                device = self._factory.create_output(info.bcm_gpio, initial_value=off_level)
            except Exception as exc:
                logger.error(
                    "CRITICAL: Cannot initialize GPIO for %s (BCM%d): %s",
                    channel.name, info.bcm_gpio, exc,
                )
                # Clean up any already-initialized devices
                self._cleanup()
                raise HardwareError(
                    ComponentId.RELAY_BOARD,
                    f"GPIO init failed for {channel.name} (BCM{info.bcm_gpio}): {exc}"
                ) from exc

            self._devices[channel] = device
            self._states[channel] = ChannelState(
                channel=channel,
                info=info,
                polarity=polarity,
            )

            logger.info(
                "  %s (BCM%d, pin %d): %s polarity, initial OFF",
                info.load_name, info.bcm_gpio, info.physical_pin, polarity.value,
            )

        self._initialized = True
        logger.info("RelayManager: all %d channels initialized to OFF", len(self._devices))

    def set_channel(
        self,
        channel: RelayChannel,
        command: ActuatorCommand,
        reason: str,
    ) -> ChannelState:
        """Command a relay channel to ON or OFF.

        This method sets the GPIO level and updates state. It does NOT
        validate safety rules — that is the safety layer's responsibility.

        Args:
            channel: Which relay channel.
            command: ON or OFF.
            reason: Why the change is being made (logged).

        Returns:
            Updated ChannelState.

        Raises:
            HardwareError: If GPIO write fails.
        """
        with self._lock:
            if channel not in self._devices:
                raise HardwareError(
                    ComponentId.RELAY_BOARD,
                    f"Channel {channel.name} not initialized"
                )

            state = self._states[channel]
            device = self._devices[channel]

            # Skip no-op
            if state.commanded == command:
                logger.debug(
                    "Relay %s already %s (no-op): %s",
                    channel.name, command.value, reason,
                )
                return state

            # Compute GPIO level for requested command
            if command == ActuatorCommand.ON:
                gpio_on = state.polarity == RelayPolarity.ACTIVE_HIGH
            else:
                gpio_on = state.polarity == RelayPolarity.ACTIVE_LOW

            try:
                if gpio_on:
                    device.on()
                else:
                    device.off()
            except Exception as exc:
                logger.error(
                    "GPIO WRITE FAILED for %s (BCM%d): %s",
                    channel.name, state.info.bcm_gpio, exc,
                )
                raise HardwareError(
                    ComponentId.RELAY_BOARD,
                    f"GPIO write failed for {channel.name}: {exc}"
                ) from exc

            now_utc = datetime.now(timezone.utc)
            mono = time.monotonic()

            old_cmd = state.commanded
            state.commanded = command
            state.last_change_utc = now_utc
            state.last_change_mono = mono
            state.command_count += 1
            state.last_reason = reason

            logger.info(
                "RELAY %s: %s → %s | reason: %s | gpio_level=%s | count=%d",
                channel.name, old_cmd.value, command.value,
                reason, "HIGH" if gpio_on else "LOW", state.command_count,
            )

            return state

    def all_off(self, reason: str) -> None:
        """Command ALL relay channels to OFF. Used for emergency/shutdown."""
        logger.warning("ALL RELAYS OFF: %s", reason)
        for channel in self._devices:
            try:
                self.set_channel(channel, ActuatorCommand.OFF, reason)
            except Exception:
                logger.exception("Failed to turn off %s during all_off", channel.name)

    def get_state(self, channel: RelayChannel) -> ChannelState | None:
        """Get the current state of a channel."""
        with self._lock:
            return self._states.get(channel)

    def get_channel_state(self, channel: RelayChannel) -> ActuatorCommand:
        """Get the commanded actuator state of a channel (defaults to OFF)."""
        state = self.get_state(channel)
        return state.commanded if state else ActuatorCommand.OFF

    def get_all_states(self) -> dict[RelayChannel, ChannelState]:
        """Get a snapshot of all channel states."""
        with self._lock:
            return dict(self._states)

    def check(self) -> DiagnosticResult:
        """Diagnostic check: verify GPIO access and all channels OFF.

        NOTE: This only verifies the control path (GPIO access). It does NOT
        confirm that relays physically switched. Without auxiliary contacts or
        current feedback, physical operation is UNVERIFIABLE.
        """
        if not self._initialized:
            return DiagnosticResult(
                component=ComponentId.RELAY_BOARD,
                status=DiagnosticStatus.FAIL,
                message="RelayManager not initialized",
            )

        all_off = all(
            s.commanded == ActuatorCommand.OFF for s in self._states.values()
        )
        if not all_off:
            return DiagnosticResult(
                component=ComponentId.RELAY_BOARD,
                status=DiagnosticStatus.FAIL,
                message="Not all channels are in OFF state during diagnostic",
            )

        return DiagnosticResult(
            component=ComponentId.RELAY_BOARD,
            status=DiagnosticStatus.UNVERIFIABLE,
            message=(
                "GPIO control path OK, all channels commanded OFF. "
                "Physical relay operation is UNVERIFIABLE without feedback hardware."
            ),
        )

    @property
    def is_initialized(self) -> bool:
        """Whether all relay GPIO outputs were successfully initialized."""
        return self._initialized

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.RELAY_BOARD

    def close(self) -> None:
        """Release all GPIO resources. Called during shutdown."""
        logger.info("RelayManager: releasing GPIO resources")
        self.all_off("shutdown cleanup")
        self._cleanup()

    def _cleanup(self) -> None:
        """Close all GPIO devices without changing state."""
        for channel, device in self._devices.items():
            try:
                device.close()
            except Exception:
                logger.exception("Error closing GPIO for %s", channel.name)
        self._devices.clear()
        self._initialized = False
