"""Actuator control policies, thresholds, and hysteresis evaluations.

Encapsulates pure decision rules for environmental control (temperature,
humidity, lighting schedule).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sericulture.config import ControlThresholds
from sericulture.event_types import (
    ActuatorCommand,
    FloatSwitchState,
    LoadId,
    MainsState,
    SensorHealth,
    SHT40Reading,
)

logger = logging.getLogger(__name__)


@dataclass
class PolicyEvaluation:
    """The result of evaluating control policies for all loads."""
    fan_command: ActuatorCommand | None = None
    fan_reason: str = ""

    mist_command: ActuatorCommand | None = None
    mist_reason: str = ""

    light_command: ActuatorCommand | None = None
    light_reason: str = ""


class EnvironmentalPolicy:
    """Evaluates sensor conditions against thresholds and hysteresis."""

    def __init__(self, thresholds: ControlThresholds):
        self._thresh = thresholds

    def evaluate_fan(
        self,
        current_fan_on: bool,
        reading: SHT40Reading | None,
    ) -> tuple[ActuatorCommand | None, str]:
        """Evaluate fan command based on temperature reading."""
        if not reading or not reading.is_valid or reading.temperature_c is None:
            if current_fan_on:
                return ActuatorCommand.OFF, "SHT40 reading invalid or missing — fail-safe fan OFF"
            return None, "SHT40 invalid — no action"

        temp = reading.temperature_c
        if not current_fan_on and temp >= self._thresh.fan_on_temp_c:
            return ActuatorCommand.ON, f"Temperature {temp:.1f}°C >= threshold {self._thresh.fan_on_temp_c}°C"
        elif current_fan_on and temp <= self._thresh.fan_off_temp_c:
            return ActuatorCommand.OFF, f"Temperature {temp:.1f}°C <= hysteresis limit {self._thresh.fan_off_temp_c}°C"

        return None, "Temperature within hysteresis band"

    def evaluate_mist(
        self,
        current_mist_on: bool,
        reading: SHT40Reading | None,
        float_state: FloatSwitchState,
    ) -> tuple[ActuatorCommand | None, str]:
        """Evaluate mist sprayer command based on humidity and water level."""
        # Hard safety: water level low or unknown prohibits misting
        if float_state != FloatSwitchState.WATER_OK:
            if current_mist_on:
                return ActuatorCommand.OFF, f"Float switch {float_state.value} — water safety shutoff"
            return None, f"Float switch {float_state.value} — mist prohibited"

        if not reading or not reading.is_valid or reading.relative_humidity_pct is None:
            if current_mist_on:
                return ActuatorCommand.OFF, "SHT40 reading invalid — fail-safe mist OFF"
            return None, "SHT40 invalid — no action"

        humidity = reading.relative_humidity_pct
        if not current_mist_on and humidity <= self._thresh.mist_on_humidity_pct:
            return ActuatorCommand.ON, f"Humidity {humidity:.1f}% <= threshold {self._thresh.mist_on_humidity_pct}%"
        elif current_mist_on and humidity >= self._thresh.mist_off_humidity_pct:
            return ActuatorCommand.OFF, f"Humidity {humidity:.1f}% >= hysteresis limit {self._thresh.mist_off_humidity_pct}%"

        return None, "Humidity within hysteresis band"

    def evaluate_light(
        self,
        current_light_on: bool,
        current_hour_utc: int | None = None,
    ) -> tuple[ActuatorCommand | None, str]:
        """Evaluate night light schedule."""
        if current_hour_utc is None:
            current_hour_utc = datetime.now(timezone.utc).hour

        on_h = self._thresh.light_on_hour
        off_h = self._thresh.light_off_hour

        # Determine if scheduled ON
        if on_h < off_h:
            should_be_on = on_h <= current_hour_utc < off_h
        else:
            # Overnights, e.g. 20:00 to 06:00
            should_be_on = current_hour_utc >= on_h or current_hour_utc < off_h

        if should_be_on and not current_light_on:
            return ActuatorCommand.ON, f"Scheduled light ON (hour={current_hour_utc} UTC)"
        elif not should_be_on and current_light_on:
            return ActuatorCommand.OFF, f"Scheduled light OFF (hour={current_hour_utc} UTC)"

        return None, "Schedule unchanged"

    def evaluate_all(
        self,
        current_fan: bool,
        current_mist: bool,
        current_light: bool,
        reading: SHT40Reading | None,
        float_state: FloatSwitchState,
        mains_state: MainsState,
    ) -> PolicyEvaluation:
        """Evaluate policy for all loads under current conditions."""
        # If mains is lost, everything must be commanded OFF
        if mains_state != MainsState.PRESENT:
            return PolicyEvaluation(
                fan_command=ActuatorCommand.OFF if current_fan else None,
                fan_reason="Mains power lost or unstable",
                mist_command=ActuatorCommand.OFF if current_mist else None,
                mist_reason="Mains power lost or unstable",
                light_command=ActuatorCommand.OFF if current_light else None,
                light_reason="Mains power lost or unstable",
            )

        f_cmd, f_reason = self.evaluate_fan(current_fan, reading)
        m_cmd, m_reason = self.evaluate_mist(current_mist, reading, float_state)
        l_cmd, l_reason = self.evaluate_light(current_light)

        return PolicyEvaluation(
            fan_command=f_cmd,
            fan_reason=f_reason,
            mist_command=m_cmd,
            mist_reason=m_reason,
            light_command=l_cmd,
            light_reason=l_reason,
        )
