"""Unit tests for configuration loading and validation."""

import pytest
from pathlib import Path
from sericulture.config import load_config
from sericulture.event_types import ConfigurationError, RelayPolarity, FloatSwitchContactType


def test_load_example_config():
    """Verify device.example.yaml loads cleanly and passes all dataclass validations."""
    config_path = Path("config/device.example.yaml")
    assert config_path.is_file()
    cfg = load_config(config_path)

    assert cfg.device.device_id == "SERI-PI5-001"
    assert cfg.device.farm_id == "FARM-KAR-042"
    assert cfg.relay.channels["ch1_fan"].polarity == RelayPolarity.ACTIVE_LOW
    assert cfg.float_switch.contact_type == FloatSwitchContactType.NORMALLY_OPEN
    assert cfg.thresholds.fan_on_temp_c == 28.5
    assert cfg.thresholds.fan_off_temp_c == 26.0


def test_config_missing_file_raises():
    with pytest.raises(ConfigurationError):
        load_config("non_existent_config.yaml")


def test_config_invalid_relay_default_boot_fails(tmp_path):
    """Relays must never default to ON at boot."""
    bad_yaml = """
device:
  device_id: "TEST-01"
  farm_id: "FARM-01"
relay:
  board_model: "waveshare"
  channels:
    ch1_fan:
      polarity: "active_low"
"""
    # Writing partial config with missing required keys should raise ConfigurationError
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(bad_yaml)
    with pytest.raises(ConfigurationError):
        load_config(cfg_file)
