"""Unit tests for isolated mains presence monitor and outage handling."""

import pytest
import time
from sericulture.config import MainsDetectorConfig
from sericulture.event_types import MainsState
from sericulture.hardware.mains_monitor import MainsMonitor, MockMainsInputDriver


def test_mains_monitor_present_and_loss_transition():
    cfg = MainsDetectorConfig(
        active_high=True,
        debounce_ms=50,
    )
    driver = MockMainsInputDriver(initial_level=1)
    monitor = MainsMonitor(cfg, driver=driver, recovery_stabilize_sec=0.1)

    assert monitor.get_state() == MainsState.PRESENT
    assert monitor.is_mains_present() is True

    lost_called = []
    monitor.add_on_lost_listener(lambda: lost_called.append(True))

    # Mains drops (pin 0)
    driver.level = 0
    monitor.get_state()  # register level change
    time.sleep(0.06)     # debounce period

    assert monitor.get_state() == MainsState.LOST
    assert monitor.is_mains_present() is False
    assert len(lost_called) == 1


def test_mains_restoration_stabilization_requirement():
    cfg = MainsDetectorConfig(
        active_high=True,
        debounce_ms=20,
    )
    driver = MockMainsInputDriver(initial_level=0)
    monitor = MainsMonitor(cfg, driver=driver, recovery_stabilize_sec=0.1)
    assert monitor.get_state() == MainsState.LOST

    # Mains returns, but needs 100ms stabilization
    driver.level = 1
    monitor.get_state()  # register level change
    time.sleep(0.03)     # debounce elapsed
    monitor.get_state()  # starts stabilization period
    assert monitor.get_state() == MainsState.LOST  # still waiting for stabilization

    time.sleep(0.12)     # stabilization elapsed
    assert monitor.get_state() == MainsState.PRESENT
