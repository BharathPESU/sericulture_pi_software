"""Unit tests for the SafetyLayer single API and actuator guards."""

import pytest
from sericulture.config import load_config
from sericulture.control.safety_layer import SafetyLayer, SensorProvider
from sericulture.event_types import (
    ActuatorCommand,
    FloatSwitchState,
    LoadId,
    MainsState,
    SensorHealth,
    SHT40Reading,
    SystemState,
)
from sericulture.hardware.relay_manager import MockGPIOFactory, RelayManager
from sericulture.state_machine import StateMachine


@pytest.fixture
def setup_safety():
    cfg = load_config("config/device.example.yaml")
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, "init")
    sm.transition_to(SystemState.DIAGNOSTICS, "init")
    sm.transition_to(SystemState.SELF_TEST, "init")
    sm.transition_to(SystemState.AUTO_READY, "init")

    relays = RelayManager(cfg.relay, gpio_factory=MockGPIOFactory())
    sensors = SensorProvider()

    # Healthy defaults
    sensors.set_sht40_provider(lambda: SHT40Reading(
        temperature_c=25.0,
        relative_humidity_pct=75.0,
        health=SensorHealth.VALID,
    ))
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_OK)
    sensors.set_mains_provider(lambda: MainsState.PRESENT)

    safety = SafetyLayer(cfg, sm, relays, sensors)
    return safety, sm, sensors


def test_safety_allows_actuator_when_all_healthy(setup_safety):
    safety, sm, sensors = setup_safety
    res = safety.request_actuator_state(LoadId.FAN, ActuatorCommand.ON, reason="High temp", source="test")
    assert res.allowed is True
    assert res.actual_command == ActuatorCommand.ON


def test_safety_blocks_mist_when_float_switch_water_low(setup_safety):
    safety, sm, sensors = setup_safety
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_LOW)

    res = safety.request_actuator_state(LoadId.MIST, ActuatorCommand.ON, reason="Low humidity", source="test")
    assert res.allowed is False
    assert res.actual_command == ActuatorCommand.OFF
    assert "float" in res.rejection_reason.lower() or "water" in res.rejection_reason.lower()


def test_safety_blocks_loads_when_mains_lost(setup_safety):
    safety, sm, sensors = setup_safety
    sensors.set_mains_provider(lambda: MainsState.LOST)

    res = safety.request_actuator_state(LoadId.FAN, ActuatorCommand.ON, reason="High temp", source="test")
    assert res.allowed is False
    assert res.actual_command == ActuatorCommand.OFF


def test_safety_blocks_actuation_in_lockout_state(setup_safety):
    safety, sm, sensors = setup_safety
    sm.transition_to(SystemState.FAULT_LOCKOUT, reason="Fault simulated")

    res = safety.request_actuator_state(LoadId.FAN, ActuatorCommand.ON, reason="High temp", source="test")
    assert res.allowed is False
    assert res.actual_command == ActuatorCommand.OFF
