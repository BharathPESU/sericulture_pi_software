"""Unit tests for non-blocking active buzzer beep patterns."""

import pytest
import time
from sericulture.config import BuzzerConfig
from sericulture.event_types import FaultCategory
from sericulture.hardware.buzzer import BuzzerController
from sericulture.hardware.relay_manager import MockGPIOFactory


def test_buzzer_play_pattern_non_blocking():
    cfg = BuzzerConfig(
        bcm_gpio=12,
        enabled=True,
        max_repeat_interval_s=60.0,
        patterns={
            "camera_fault": [(0.02, 0.02)],
        },
    )
    mock_factory = MockGPIOFactory()
    buzzer = BuzzerController(cfg, gpio_factory=mock_factory)
    buzzer.initialize()
    buzzer.start()

    # Calling play_fault should return immediately without blocking
    start = time.monotonic()
    accepted = buzzer.play_fault(FaultCategory.CAMERA_FAULT)
    elapsed = time.monotonic() - start
    assert accepted is True
    assert elapsed < 0.05, "play_fault must be non-blocking"

    # Stop buzzer cleanly
    buzzer.stop()
