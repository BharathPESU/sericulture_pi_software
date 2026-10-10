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


def test_env_file_loading_and_overrides(tmp_path, monkeypatch):
    """Verify .env file variables load and override configuration properly."""
    env_content = """
    SERICULTURE_DEVICE_ID=CUSTOM-PI5-999
    SERICULTURE_FARM_ID=CUSTOM-FARM-888
    AWS_IOT_ENDPOINT=custom-ats.iot.ap-south-1.amazonaws.com
    AWS_IOT_REGION=ap-south-1
    AWS_IOT_CLIENT_ID=CUSTOM-PI5-999
    AWS_IOT_CERT_PATH=/custom/certs/cert.pem
    AWS_IOT_KEY_PATH=/custom/certs/key.pem
    AWS_IOT_ROOT_CA_PATH=/custom/certs/ca.pem
    AWS_IOT_ENABLED=true
    AWS_IOT_QOS=1
    AWS_IOT_BATCH_SIZE=25
    SERICULTURE_LOG_LEVEL=DEBUG
    """
    env_file = tmp_path / ".env"
    env_file.write_text(env_content)

    monkeypatch.setenv("SERICULTURE_ENV_PATH", str(env_file))

    cfg = load_config("config/device.example.yaml")

    assert cfg.device.device_id == "CUSTOM-PI5-999"
    assert cfg.device.farm_id == "CUSTOM-FARM-888"
    assert cfg.cloud.endpoint == "custom-ats.iot.ap-south-1.amazonaws.com"
    assert cfg.cloud.client_id == "CUSTOM-PI5-999"
    assert cfg.cloud.cert_path == "/custom/certs/cert.pem"
    assert cfg.cloud.key_path == "/custom/certs/key.pem"
    assert cfg.cloud.root_ca_path == "/custom/certs/ca.pem"
    assert cfg.cloud.enabled is True
    assert cfg.cloud.qos == 1
    assert cfg.cloud.batch_size == 25
    assert cfg.log_level == "DEBUG"

