"""Decision engine — translates environmental conditions into safety layer requests.

SAFETY RULES:
  - Dispatches all actuator requests through the SafetyLayer API.
  - Never accesses RelayManager or GPIO directly.
  - Automatically inhibits control if system is in FAULT_LOCKOUT or MAINS_LOST.
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Callable

from sericulture.control.actuator_policy import EnvironmentalPolicy, PolicyEvaluation
from sericulture.control.safety_layer import SafetyLayer, SensorProvider
from sericulture.event_types import (
    ActuatorCommand,
    ActuatorResult,
    FloatSwitchState,
    LoadId,
    MainsState,
    SystemState,
)

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Evaluates policies and requests actuator state transitions via the SafetyLayer."""

    def __init__(
        self,
        safety_layer: SafetyLayer,
        sensor_provider: SensorProvider,
        policy: EnvironmentalPolicy,
    ):
        self._safety = safety_layer
        self._sensors = sensor_provider
        self._policy = policy
        self._lock = Lock()
        self._on_result_callbacks: list[Callable[[ActuatorResult], None]] = []

    def add_result_listener(self, cb: Callable[[ActuatorResult], None]) -> None:
        self._on_result_callbacks.append(cb)

    def step(self) -> list[ActuatorResult]:
        """Execute one evaluation cycle."""
        with self._lock:
            # 1. Fetch current sensor states
            sht40 = self._sensors.get_sht40()
            float_st = self._sensors.get_float_switch()
            mains_st = self._sensors.get_mains()

            # 2. Get current load states from safety layer
            states = self._safety.get_all_states()
            fan_on = states.get(LoadId.FAN) == ActuatorCommand.ON
            mist_on = states.get(LoadId.MIST) == ActuatorCommand.ON
            light_on = states.get(LoadId.NIGHT_LIGHT) == ActuatorCommand.ON

            # 3. Evaluate policies
            eval_res: PolicyEvaluation = self._policy.evaluate_all(
                current_fan=fan_on,
                current_mist=mist_on,
                current_light=light_on,
                reading=sht40,
                float_state=float_st,
                mains_state=mains_st,
            )

            results: list[ActuatorResult] = []

            # 4. Dispatch requests through SafetyLayer
            if eval_res.fan_command is not None:
                res = self._safety.request_actuator_state(
                    load=LoadId.FAN,
                    desired=eval_res.fan_command,
                    reason=eval_res.fan_reason,
                    source="decision_engine",
                )
                results.append(res)

            if eval_res.mist_command is not None:
                res = self._safety.request_actuator_state(
                    load=LoadId.MIST,
                    desired=eval_res.mist_command,
                    reason=eval_res.mist_reason,
                    source="decision_engine",
                )
                results.append(res)

            if eval_res.light_command is not None:
                res = self._safety.request_actuator_state(
                    load=LoadId.NIGHT_LIGHT,
                    desired=eval_res.light_command,
                    reason=eval_res.light_reason,
                    source="decision_engine",
                )
                results.append(res)

            for res in results:
                for cb in self._on_result_callbacks:
                    try:
                        cb(res)
                    except Exception as e:
                        logger.error(f"Error in decision result callback: {e}")

            return results

    def handle_mains_lost(self) -> list[ActuatorResult]:
        """Emergency handler invoked immediately when mains loss is confirmed."""
        with self._lock:
            logger.warning("DecisionEngine handling MAINS LOST — shutting down all loads.")
            res = self._safety.force_all_off(reason="Mains loss emergency shutoff")
            for r in res:
                for cb in self._on_result_callbacks:
                    try:
                        cb(r)
                    except Exception as e:
                        logger.error(f"Error in decision mains-loss callback: {e}")
            return res
