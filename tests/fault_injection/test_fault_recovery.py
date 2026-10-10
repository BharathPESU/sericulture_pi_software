"""Fault injection and recovery tests."""

import pytest
import time
from sericulture.config import load_config
from sericulture.control.actuator_policy import EnvironmentalPolicy
from sericulture.control.decision_engine import DecisionEngine
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
from sericulture.hardware.mains_monitor import MainsMonitor, MockMainsInputDriver
from sericulture.hardware.pin_map import RelayChannel
from sericulture.hardware.relay_manager import MockGPIOFactory, RelayManager
from sericulture.state_machine import StateMachine


def test_mains_failure_forces_all_loads_off():
    cfg = load_config("config/device.example.yaml")
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, "init")
    sm.transition_to(SystemState.DIAGNOSTICS, "init")
    sm.transition_to(SystemState.SELF_TEST, "init")
    sm.transition_to(SystemState.AUTO_READY, "init")

    relays = RelayManager(cfg.relay, gpio_factory=MockGPIOFactory())
    sensors = SensorProvider()

    sensors.set_sht40_provider(lambda: SHT40Reading(temperature_c=32.0, relative_humidity_pct=60.0, health=SensorHealth.VALID))
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_OK)
    sensors.set_mains_provider(lambda: MainsState.PRESENT)

    safety = SafetyLayer(cfg, sm, relays, sensors)
    policy = EnvironmentalPolicy(cfg.thresholds)
    engine = DecisionEngine(safety, sensors, policy)

    # 1. Turn on Fan due to high temp (32°C > 28.5°C)
    engine.step()
    assert safety.get_load_state(LoadId.FAN) == ActuatorCommand.ON

    # 2. Inject Mains Failure
    sensors.set_mains_provider(lambda: MainsState.LOST)
    engine.handle_mains_lost()

    # 3. All loads must immediately be OFF
    assert safety.get_load_state(LoadId.FAN) == ActuatorCommand.OFF
    assert safety.get_load_state(LoadId.MIST) == ActuatorCommand.OFF
    assert safety.get_load_state(LoadId.NIGHT_LIGHT) == ActuatorCommand.OFF


def test_water_low_injection_forces_mist_off():
    cfg = load_config("config/device.example.yaml")
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, "init")
    sm.transition_to(SystemState.DIAGNOSTICS, "init")
    sm.transition_to(SystemState.SELF_TEST, "init")
    sm.transition_to(SystemState.AUTO_READY, "init")

    relays = RelayManager(cfg.relay, gpio_factory=MockGPIOFactory())
    sensors = SensorProvider()

    # Low humidity (55% < 70%) requires mist
    sensors.set_sht40_provider(lambda: SHT40Reading(temperature_c=25.0, relative_humidity_pct=55.0, health=SensorHealth.VALID))
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_OK)
    sensors.set_mains_provider(lambda: MainsState.PRESENT)

    safety = SafetyLayer(cfg, sm, relays, sensors)
    policy = EnvironmentalPolicy(cfg.thresholds)
    engine = DecisionEngine(safety, sensors, policy)

    # 1. Mist should turn ON
    engine.step()
    assert safety.get_load_state(LoadId.MIST) == ActuatorCommand.ON

    # 2. Inject Water Low fault
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_LOW)
    engine.step()

    # 3. Mist must immediately turn OFF to prevent pump burnout
    assert safety.get_load_state(LoadId.MIST) == ActuatorCommand.OFF


def test_sensor_failure_forces_actuators_off():
    cfg = load_config("config/device.example.yaml")
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, "init")
    sm.transition_to(SystemState.DIAGNOSTICS, "init")
    sm.transition_to(SystemState.SELF_TEST, "init")
    sm.transition_to(SystemState.AUTO_READY, "init")

    relays = RelayManager(cfg.relay, gpio_factory=MockGPIOFactory())
    sensors = SensorProvider()

    # Initial high temp turning fan on
    sensors.set_sht40_provider(lambda: SHT40Reading(temperature_c=31.0, relative_humidity_pct=75.0, health=SensorHealth.VALID))
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_OK)
    sensors.set_mains_provider(lambda: MainsState.PRESENT)

    safety = SafetyLayer(cfg, sm, relays, sensors)
    policy = EnvironmentalPolicy(cfg.thresholds)
    engine = DecisionEngine(safety, sensors, policy)

    engine.step()
    assert safety.get_load_state(LoadId.FAN) == ActuatorCommand.ON

    # Inject sensor error / communication loss
    sensors.set_sht40_provider(lambda: SHT40Reading(temperature_c=0.0, relative_humidity_pct=0.0, health=SensorHealth.READ_ERROR))
    engine.step()

    # Fan must shut down because sensor data is invalid
    assert safety.get_load_state(LoadId.FAN) == ActuatorCommand.OFF


def test_fault_lockout_forces_all_loads_off():
    cfg = load_config("config/device.example.yaml")
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, "init")
    sm.transition_to(SystemState.DIAGNOSTICS, "init")
    sm.transition_to(SystemState.SELF_TEST, "init")
    sm.transition_to(SystemState.AUTO_READY, "init")

    relays = RelayManager(cfg.relay, gpio_factory=MockGPIOFactory())
    sensors = SensorProvider()

    sensors.set_sht40_provider(lambda: SHT40Reading(temperature_c=32.0, relative_humidity_pct=50.0, health=SensorHealth.VALID))
    sensors.set_float_switch_provider(lambda: FloatSwitchState.WATER_OK)
    sensors.set_mains_provider(lambda: MainsState.PRESENT)

    safety = SafetyLayer(cfg, sm, relays, sensors)
    policy = EnvironmentalPolicy(cfg.thresholds)
    engine = DecisionEngine(safety, sensors, policy)

    engine.step()
    assert safety.get_load_state(LoadId.FAN) == ActuatorCommand.ON

    # Transition to FAULT_LOCKOUT
    sm.transition_to(SystemState.FAULT_LOCKOUT, "Simulated critical failure")

    # Requesting any load ON must now be rejected by SafetyLayer (allowed=False)
    res = safety.request_actuator_state(LoadId.FAN, ActuatorCommand.ON, source="decision_engine", reason="test")
    assert res.allowed is False
    assert res.actual_command == ActuatorCommand.OFF

    # SafetyLayer force_all_off ensures all relays are physically commanded OFF
    safety.force_all_off("fault lockout")
    assert safety.get_load_state(LoadId.FAN) == ActuatorCommand.OFF

