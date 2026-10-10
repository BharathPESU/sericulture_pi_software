"""Unit tests for FloatSwitchMonitor debounce and contact polarity."""

import pytest
import time
from sericulture.config import FloatSwitchConfig
from sericulture.event_types import FloatSwitchContactType, FloatSwitchState
from sericulture.hardware.float_switch import FloatSwitchMonitor, MockDigitalInputDriver


def test_float_switch_no_pullup_normal_operation():
    cfg = FloatSwitchConfig(
        contact_type=FloatSwitchContactType.NORMALLY_OPEN,
        pull_up=True,
        debounce_ms=50,
        water_ok_when_closed=True,
    )
    # Pull-up: contact closed = pin 0 (LOW) -> WATER_OK
    driver = MockDigitalInputDriver(initial_level=0)
    monitor = FloatSwitchMonitor(cfg, driver=driver)

    assert monitor.get_state() == FloatSwitchState.WATER_OK
    assert monitor.is_water_level_ok() is True

    # Water drops -> switch opens -> pin pulled HIGH (1)
    driver.level = 1
    monitor.get_state()  # register change
    time.sleep(0.06)     # debounce period
    assert monitor.get_state() == FloatSwitchState.WATER_LOW
    assert monitor.is_water_level_ok() is False


def test_float_switch_nc_inversion():
    cfg = FloatSwitchConfig(
        contact_type=FloatSwitchContactType.NORMALLY_CLOSED,
        pull_up=True,
        debounce_ms=10,
        water_ok_when_closed=True,
    )
    driver = MockDigitalInputDriver(initial_level=1)
    monitor = FloatSwitchMonitor(cfg, driver=driver)
    monitor.get_state()
    time.sleep(0.02)
    assert monitor.get_state() == FloatSwitchState.WATER_OK
