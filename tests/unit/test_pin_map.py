"""Unit tests for fixed pin mapping and validation rules."""

import pytest
from sericulture.hardware.pin_map import (
    BCM_BUZZER,
    BCM_FLOAT_SWITCH,
    BCM_I2C_SCL,
    BCM_I2C_SDA,
    BCM_MAINS_DETECTOR,
    BCM_RELAY_CH1_FAN,
    BCM_RELAY_CH2_MIST,
    BCM_RELAY_CH3_LIGHT,
    BCM_RELAY_CH4_SPARE,
    BCM_RELAY_CH5_SPARE,
    BCM_RELAY_CH6_SPARE,
    PIN_MAP,
    validate_no_pin_conflicts,
)


def test_fixed_bcm_pin_constants():
    """Verify fixed BCM mappings match the prompt specification V1."""
    assert BCM_I2C_SDA == 2
    assert BCM_I2C_SCL == 3
    assert BCM_FLOAT_SWITCH == 22
    assert BCM_RELAY_CH1_FAN == 5
    assert BCM_RELAY_CH2_MIST == 6
    assert BCM_RELAY_CH3_LIGHT == 13
    assert BCM_RELAY_CH4_SPARE == 16
    assert BCM_RELAY_CH5_SPARE == 19
    assert BCM_RELAY_CH6_SPARE == 20
    assert BCM_BUZZER == 12
    assert BCM_MAINS_DETECTOR == 26


def test_no_pin_overlap():
    """Verify that no two functions share the same physical header pin."""
    used_physical = [p.physical_pin for p in PIN_MAP if p.physical_pin > 0]
    assert len(used_physical) == len(set(used_physical)), "Physical pins must be unique"


def test_validate_pin_assignments_passes():
    """Validation helper must return empty list of conflicts."""
    assert validate_no_pin_conflicts() == []
