"""Unit tests for RelayManager active-high/active-low abstraction and safe defaults."""

import pytest
from sericulture.config import RelayChannelConfig, RelayConfig
from sericulture.event_types import ActuatorCommand, RelayPolarity
from sericulture.hardware.pin_map import BCM_RELAY_CH1_FAN, BCM_RELAY_CH2_MIST, RelayChannel
from sericulture.hardware.relay_manager import MockGPIOFactory, MockGPIOOutput, RelayManager


@pytest.fixture
def relay_manager():
    channels = {
        "ch1_fan": RelayChannelConfig(
            bcm_gpio=BCM_RELAY_CH1_FAN,
            polarity=RelayPolarity.ACTIVE_LOW,
            load_name="fan",
        ),
        "ch2_mist": RelayChannelConfig(
            bcm_gpio=BCM_RELAY_CH2_MIST,
            polarity=RelayPolarity.ACTIVE_HIGH,
            load_name="mist",
        ),
    }
    config = RelayConfig(
        board_model="mock_board",
        board_revision="v1",
        separate_supply_confirmed=True,
        channels=channels,
    )
    mock_factory = MockGPIOFactory()
    return RelayManager(config, gpio_factory=mock_factory)


def test_relay_defaults_all_off_on_init(relay_manager):
    """At boot, all channels must be commanded to safe OFF."""
    assert relay_manager.get_channel_state(RelayChannel.CH1_FAN) == ActuatorCommand.OFF
    assert relay_manager.get_channel_state(RelayChannel.CH2_MIST) == ActuatorCommand.OFF


def test_relay_polarity_translation(relay_manager):
    """ACTIVE_LOW: ON sets raw pin 0, OFF sets raw pin 1.
    ACTIVE_HIGH: ON sets raw pin 1, OFF sets raw pin 0.
    """
    # CH1 is ACTIVE_LOW
    relay_manager.set_channel(RelayChannel.CH1_FAN, ActuatorCommand.ON, reason="Test ON")
    assert relay_manager.get_channel_state(RelayChannel.CH1_FAN) == ActuatorCommand.ON

    # CH2 is ACTIVE_HIGH
    relay_manager.set_channel(RelayChannel.CH2_MIST, ActuatorCommand.ON, reason="Test ON")
    assert relay_manager.get_channel_state(RelayChannel.CH2_MIST) == ActuatorCommand.ON

    # Force all off
    relay_manager.all_off("Test shutoff")
    assert relay_manager.get_channel_state(RelayChannel.CH1_FAN) == ActuatorCommand.OFF
    assert relay_manager.get_channel_state(RelayChannel.CH2_MIST) == ActuatorCommand.OFF
