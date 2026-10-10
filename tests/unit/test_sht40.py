"""Unit tests for SHT40 sensor reading, validation, and error recovery."""

import pytest
import math
from sericulture.config import SHT40Config
from sericulture.event_types import SensorHealth
from sericulture.hardware.sht40 import MockI2CDriver, SHT40Sensor


@pytest.fixture
def sht40():
    config = SHT40Config(
        read_interval_s=1.0,
        stale_timeout_s=5.0,
        temp_min_c=10.0,
        temp_max_c=45.0,
        humidity_min_pct=20.0,
        humidity_max_pct=100.0,
    )
    driver = MockI2CDriver(temp=26.5, humidity=75.0)
    return SHT40Sensor(config, driver=driver), driver


def test_sht40_valid_reading(sht40):
    sensor, driver = sht40
    reading = sensor.read()
    assert reading.is_valid is True
    assert reading.temperature_c == 26.5
    assert reading.relative_humidity_pct == 75.0
    assert reading.health == SensorHealth.VALID


def test_sht40_out_of_range_temperature(sht40):
    sensor, driver = sht40
    driver.temp = 55.0  # Above 45.0 max
    reading = sensor.read()
    assert reading.is_valid is False
    assert reading.health == SensorHealth.OUT_OF_RANGE


def test_sht40_nan_reading(sht40):
    sensor, driver = sht40
    driver.temp = float("nan")
    reading = sensor.read()
    assert reading.is_valid is False
    assert reading.health == SensorHealth.OUT_OF_RANGE


def test_sht40_communication_failure_and_recovery(sht40):
    sensor, driver = sht40
    driver.fail_next = True
    reading = sensor.read()
    assert reading.is_valid is False
    assert reading.health == SensorHealth.READ_ERROR

    # Next reading succeeds
    reading2 = sensor.read()
    assert reading2.is_valid is True
