"""Safety layer — single entry point for all actuator state changes.

ALL actuator commands MUST go through request_actuator_state(). No other
module is allowed to call RelayManager.set_channel() directly.

This layer validates:
  - System state allows the operation
  - Required sensors are healthy and fresh
  - Float switch allows misting
  - Mains presence permits operation
  - Manual override priority
  - Timing constraints (min on/off, max run, cooldown)
  - Fault/lockout/reboot prevents unsafe switching
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Callable

from sericulture.config import AppConfig, ControlThresholds
from sericulture.event_types import (
    ActuatorCommand,
    ActuatorRequest,
    ActuatorResult,
    FloatSwitchState,
    LoadId,
    MainsState,
    SensorHealth,
    SHT40Reading,
    SystemState,
)
from sericulture.hardware.pin_map import RelayChannel
from sericulture.hardware.relay_manager import RelayManager
from sericulture.state_machine import StateMachine

logger = logging.getLogger(__name__)


# Map LoadId to RelayChannel
_LOAD_TO_CHANNEL: dict[LoadId, RelayChannel] = {
    LoadId.FAN:         RelayChannel.CH1_FAN,
    LoadId.MIST:        RelayChannel.CH2_MIST,
    LoadId.NIGHT_LIGHT: RelayChannel.CH3_LIGHT,
    LoadId.SPARE_CH4:   RelayChannel.CH4_SPARE,
    LoadId.SPARE_CH5:   RelayChannel.CH5_SPARE,
    LoadId.SPARE_CH6:   RelayChannel.CH6_SPARE,
}


class SensorProvider:
    """Provides current sensor readings to the safety layer.

    Modules that produce sensor data register their getters here.
    The safety layer queries them to validate actuator requests.
    """

    def __init__(self):
        self._sht40_getter: Callable[[], SHT40Reading | None] | None = None
        self._float_switch_getter: Callable[[], FloatSwitchState] | None = None
        self._mains_getter: Callable[[], MainsState] | None = None

    def set_sht40_provider(self, getter: Callable[[], SHT40Reading | None]) -> None:
        self._sht40_getter = getter

    def set_float_switch_provider(self, getter: Callable[[], FloatSwitchState]) -> None:
        self._float_switch_getter = getter

    def set_mains_provider(self, getter: Callable[[], MainsState]) -> None:
        self._mains_getter = getter

    def get_sht40(self) -> SHT40Reading | None:
        if self._sht40_getter:
            return self._sht40_getter()
        return None

    def get_float_switch(self) -> FloatSwitchState:
        if self._float_switch_getter:
            return self._float_switch_getter()
        return FloatSwitchState.UNKNOWN

    def get_mains(self) -> MainsState:
        if self._mains_getter:
            return self._mains_getter()
        return MainsState.UNKNOWN


@dataclass
class LoadTimingState:
    """Tracks timing constraints for one load."""
    last_on_mono: float = 0.0
    last_off_mono: float = 0.0
    continuous_on_since_mono: float = 0.0
    in_cooldown: bool = False
    cooldown_until_mono: float = 0.0


class SafetyLayer:
    """Validates and executes actuator requests.

    Usage:
        safety = SafetyLayer(config, state_machine, relay_manager, sensors)
        result = safety.request_actuator_state(request)
        if not result.allowed:
            logger.warning("Blocked: %s", result.rejection_reason)
    """

    def __init__(
        self,
        config: AppConfig,
        state_machine: StateMachine,
        relay_manager: RelayManager,
        sensors: SensorProvider,
    ):
        self._config = config
        self._sm = state_machine
        self._relays = relay_manager
        self._sensors = sensors
        self._lock = Lock()
        self._timing: dict[LoadId, LoadTimingState] = {
            load: LoadTimingState() for load in LoadId
        }
        self._listeners: list[Callable[[ActuatorResult], None]] = []

    def add_listener(self, callback: Callable[[ActuatorResult], None]) -> None:
        """Register a callback for actuator results (for logging/events)."""
        self._listeners.append(callback)

    def request_actuator_state(
        self,
        load: ActuatorRequest | LoadId,
        desired: ActuatorCommand | None = None,
        reason: str = "",
        source: str = "safety_layer",
    ) -> ActuatorResult:
        """Validate and execute an actuator state change request.

        This is the SINGLE entry point for all load control. No module
        should call relay_manager.set_channel() directly.
        """
        if isinstance(load, ActuatorRequest):
            request = load
        else:
            if desired is None:
                raise ValueError("desired command must be provided when passing LoadId")
            request = ActuatorRequest(
                load=load,
                desired=desired,
                reason=reason,
                source=source,
            )
        with self._lock:
            return self._process_request(request)

    def get_load_state(self, load: LoadId) -> ActuatorCommand:
        """Get the current commanded state of a load."""
        channel = _LOAD_TO_CHANNEL.get(load)
        if not channel:
            return ActuatorCommand.OFF
        return self._relays.get_channel_state(channel)

    def get_all_states(self) -> dict[LoadId, ActuatorCommand]:
        """Get current commanded states of all loads."""
        return {load: self.get_load_state(load) for load in LoadId}

    def force_all_off(self, reason: str = "") -> list[ActuatorResult]:
        """Command all loads OFF immediately and return ActuatorResults."""
        self.emergency_all_off(reason)
        results: list[ActuatorResult] = []
        for load in LoadId:
            req = ActuatorRequest(
                load=load,
                desired=ActuatorCommand.OFF,
                reason=reason,
                source="safety_layer",
            )
            results.append(ActuatorResult(
                request=req,
                allowed=True,
                actual_command=ActuatorCommand.OFF,
            ))
        return results

    def emergency_all_off(self, reason: str) -> None:
        """Emergency shutdown — bypass normal validation and turn everything off."""
        logger.critical("EMERGENCY ALL OFF: %s", reason)
        with self._lock:
            self._relays.all_off(f"EMERGENCY: {reason}")
            for load in LoadId:
                timing = self._timing[load]
                timing.last_off_mono = time.monotonic()

    def _process_request(self, request: ActuatorRequest) -> ActuatorResult:
        """Core validation and execution logic."""
        load = request.load
        desired = request.desired
        channel = _LOAD_TO_CHANNEL.get(load)

        if channel is None:
            return self._reject(request, f"Unknown load: {load.value}")

        # --- State machine check ---
        current_state = self._sm.current_state

        if desired == ActuatorCommand.ON:
            # ON requests require ready state (AUTO_READY or MANUAL_READY)
            if current_state not in (SystemState.AUTO_READY, SystemState.MANUAL_READY):
                return self._reject(
                    request,
                    f"State {current_state.value} does not permit actuators to be ON",
                )

            if current_state == SystemState.AUTO_READY and request.source in ("decision_engine", "test", "manual", "safety_layer"):
                pass  # Allowed in AUTO_READY
            elif current_state == SystemState.MANUAL_READY and request.source in ("manual", "test", "safety_layer"):
                pass  # Allowed in MANUAL_READY
            else:
                return self._reject(
                    request,
                    f"State {current_state.value} does not allow ON command from source '{request.source}'",
                )

            # Spare channels must remain OFF
            if load in (LoadId.SPARE_CH4, LoadId.SPARE_CH5, LoadId.SPARE_CH6):
                return self._reject(request, "Spare channels must remain OFF")

            # --- Sensor validation for ON commands ---
            rejection = self._validate_sensors_for_on(load)
            if rejection:
                return self._reject(request, rejection)

            # --- Timing constraints ---
            rejection = self._validate_timing(load, desired)
            if rejection:
                return self._reject(request, rejection)

        elif desired == ActuatorCommand.OFF:
            # OFF requests are almost always allowed (safety priority)
            # But still log the reason
            pass

        # --- Execute the relay command ---
        try:
            self._relays.set_channel(channel, desired, request.reason)
        except Exception as exc:
            logger.error("Relay command failed: %s", exc)
            return self._reject(request, f"Relay command failed: {exc}")

        # Update timing state
        timing = self._timing[load]
        mono_now = time.monotonic()
        if desired == ActuatorCommand.ON:
            timing.last_on_mono = mono_now
            if timing.continuous_on_since_mono == 0.0:
                timing.continuous_on_since_mono = mono_now
        else:
            timing.last_off_mono = mono_now
            timing.continuous_on_since_mono = 0.0

        result = ActuatorResult(
            request=request,
            allowed=True,
            actual_command=desired,
            relay_feedback="UNVERIFIABLE",
        )

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(result)
            except Exception:
                logger.exception("Actuator result listener error")

        logger.info(
            "ACTUATOR %s → %s | reason: %s | source: %s",
            load.value, desired.value, request.reason, request.source,
        )
        return result

    def _validate_sensors_for_on(self, load: LoadId) -> str | None:
        """Check sensor health before allowing an ON command.

        Returns rejection reason string, or None if OK.
        """
        # Mains must be present for any load
        mains = self._sensors.get_mains()
        if mains == MainsState.LOST:
            return "Mains power lost — loads must remain OFF"
        if mains == MainsState.UNKNOWN:
            return "Mains state unknown — cannot safely enable loads"

        # SHT40 must be healthy for temperature/humidity-driven loads
        if load in (LoadId.FAN, LoadId.MIST):
            reading = self._sensors.get_sht40()
            if reading is None:
                return "SHT40 reading unavailable — cannot validate conditions"
            if not reading.is_valid:
                return f"SHT40 reading unhealthy ({reading.health.value}) — refusing actuation"
            # Check staleness
            if reading.health == SensorHealth.STALE:
                return "SHT40 reading is stale — refusing actuation"

        # Float switch must show water OK for misting
        if load == LoadId.MIST:
            float_state = self._sensors.get_float_switch()
            if float_state == FloatSwitchState.WATER_LOW:
                return "Water level LOW — mist pump not allowed"
            if float_state in (FloatSwitchState.UNKNOWN, FloatSwitchState.FAULT):
                return f"Float switch state {float_state.value} — mist pump not allowed"

        return None

    def _validate_timing(self, load: LoadId, desired: ActuatorCommand) -> str | None:
        """Check timing constraints (min on/off, max run, cooldown).

        Returns rejection reason string, or None if OK.
        """
        thresholds = self._config.control
        timing = self._timing[load]
        mono_now = time.monotonic()

        if desired == ActuatorCommand.ON:
            # Cooldown check
            if timing.in_cooldown and mono_now < timing.cooldown_until_mono:
                remaining = timing.cooldown_until_mono - mono_now
                return f"Load {load.value} in cooldown ({remaining:.0f}s remaining)"

            # Min OFF time
            if timing.last_off_mono > 0:
                off_duration = mono_now - timing.last_off_mono
                if off_duration < thresholds.min_off_time_s:
                    return (
                        f"Min OFF time not met for {load.value}: "
                        f"{off_duration:.1f}s < {thresholds.min_off_time_s}s"
                    )

            timing.in_cooldown = False

        elif desired == ActuatorCommand.OFF:
            # Min ON time (only warn, don't block OFF for safety)
            if timing.last_on_mono > 0:
                on_duration = mono_now - timing.last_on_mono
                if on_duration < thresholds.min_on_time_s:
                    logger.debug(
                        "Min ON time not met for %s: %.1fs < %ss (allowing OFF for safety)",
                        load.value, on_duration, thresholds.min_on_time_s,
                    )

        return None

    def check_max_run_times(self) -> list[ActuatorResult]:
        """Periodic check: enforce maximum continuous run time.

        Call this from the watchdog/health loop. Returns results for any
        loads that were forced OFF due to exceeding max run time.
        """
        results: list[ActuatorResult] = []
        thresholds = self._config.control
        mono_now = time.monotonic()

        with self._lock:
            for load, timing in self._timing.items():
                if timing.continuous_on_since_mono > 0:
                    run_duration = mono_now - timing.continuous_on_since_mono
                    if run_duration >= thresholds.max_continuous_run_s:
                        logger.warning(
                            "Max run time exceeded for %s: %.0fs >= %.0fs",
                            load.value, run_duration, thresholds.max_continuous_run_s,
                        )
                        # Force OFF
                        channel = _LOAD_TO_CHANNEL[load]
                        try:
                            self._relays.set_channel(
                                channel, ActuatorCommand.OFF,
                                f"Max continuous run time ({thresholds.max_continuous_run_s}s) exceeded"
                            )
                        except Exception:
                            logger.exception("Failed to enforce max run time for %s", load.value)
                            continue

                        timing.last_off_mono = mono_now
                        timing.continuous_on_since_mono = 0.0
                        timing.in_cooldown = True
                        timing.cooldown_until_mono = (
                            mono_now + thresholds.cooldown_after_max_run_s
                        )

                        request = ActuatorRequest(
                            load=load,
                            desired=ActuatorCommand.OFF,
                            reason="Max continuous run time exceeded",
                            source="safety_layer",
                        )
                        results.append(ActuatorResult(
                            request=request,
                            allowed=True,
                            actual_command=ActuatorCommand.OFF,
                        ))

        return results

    def _reject(self, request: ActuatorRequest, reason: str) -> ActuatorResult:
        """Create a rejected ActuatorResult."""
        logger.warning(
            "SAFETY BLOCKED: %s %s — %s (source: %s)",
            request.load.value, request.desired.value, reason, request.source,
        )
        return ActuatorResult(
            request=request,
            allowed=False,
            actual_command=ActuatorCommand.OFF,
            rejection_reason=reason,
        )
