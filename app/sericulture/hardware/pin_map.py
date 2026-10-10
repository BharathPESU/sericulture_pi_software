"""Authoritative V1 fixed pin map for the sericulture controller.

This module is the SINGLE SOURCE OF TRUTH for all Raspberry Pi 40-pin header
assignments. No other module may hardcode BCM pin numbers.

SAFETY: Do not reassign pins in application code. If the actual relay board
datasheet or electrical review proves that a mapping is incompatible, STOP
and request a reviewed hardware revision.

All BCM numbers refer to the Broadcom SoC GPIO numbering used by gpiozero/lgpio.
Physical pin numbers are documented for wiring reference only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple


class PinDirection(Enum):
    """GPIO pin direction."""
    INPUT = "input"
    OUTPUT = "output"
    I2C = "i2c"
    POWER = "power"
    GROUND = "ground"
    RESERVED = "reserved"


class PinEntry(NamedTuple):
    """One row of the V1 fixed pin map."""
    physical_pin: int
    bcm_gpio: int | None      # None for power/ground/reserved pins
    direction: PinDirection
    assignment: str
    notes: str


# ──────────────────────────────────────────────────────────────────────
# V1 FIXED PIN MAP — do not modify without hardware review
# ──────────────────────────────────────────────────────────────────────

PIN_MAP: list[PinEntry] = [
    # --- Power / Ground ---
    PinEntry(1,  None, PinDirection.POWER,    "SHT40 VCC (3.3V)",
             "Only if sensor breakout accepts 3.3V"),
    PinEntry(6,  None, PinDirection.GROUND,   "SHT40 GND", ""),
    PinEntry(9,  None, PinDirection.GROUND,   "Float-switch contact return",
             "If wired for configured pull-up circuit"),
    PinEntry(39, None, PinDirection.GROUND,   "Isolated detector logic GND",
             "Only if appropriate for detector output design"),

    # --- I²C (SHT40) ---
    PinEntry(3,  2,  PinDirection.I2C,        "SHT40 SDA (I2C1)", ""),
    PinEntry(5,  3,  PinDirection.I2C,        "SHT40 SCL (I2C1)", ""),

    # --- Digital Inputs ---
    PinEntry(15, 22, PinDirection.INPUT,      "Float-switch state",
             "NO/NC type configured in device.yaml"),
    PinEntry(37, 26, PinDirection.INPUT,      "Isolated mains-presence detector",
             "Low-voltage logic output only — never connect mains to GPIO"),

    # --- Digital Outputs (Relays) ---
    PinEntry(29, 5,  PinDirection.OUTPUT,     "Relay CH1 — Fan", ""),
    PinEntry(31, 6,  PinDirection.OUTPUT,     "Relay CH2 — Mist sprayer/pump", ""),
    PinEntry(33, 13, PinDirection.OUTPUT,     "Relay CH3 — Night light", ""),
    PinEntry(36, 16, PinDirection.OUTPUT,     "Relay CH4 — Reserved (OFF)", ""),
    PinEntry(35, 19, PinDirection.OUTPUT,     "Relay CH5 — Reserved (OFF)", ""),
    PinEntry(38, 20, PinDirection.OUTPUT,     "Relay CH6 — Reserved (OFF)", ""),

    # --- Digital Output (Buzzer) ---
    PinEntry(32, 12, PinDirection.OUTPUT,     "Active buzzer control",
             "Use transistor/driver if buzzer current exceeds GPIO source capability"),

    # --- Reserved (HAT ID) ---
    PinEntry(27, None, PinDirection.RESERVED, "HAT ID EEPROM",
             "Do not allocate to application functions"),
    PinEntry(28, None, PinDirection.RESERVED, "HAT ID EEPROM",
             "Do not allocate to application functions"),
]

# ──────────────────────────────────────────────────────────────────────
# Named BCM constants — use these throughout the application
# ──────────────────────────────────────────────────────────────────────

# I²C (handled by kernel driver, listed for documentation)
BCM_I2C_SDA = 2
BCM_I2C_SCL = 3

# Digital inputs
BCM_FLOAT_SWITCH = 22
BCM_MAINS_DETECTOR = 26

# Relay outputs
BCM_RELAY_CH1_FAN = 5
BCM_RELAY_CH2_MIST = 6
BCM_RELAY_CH3_LIGHT = 13
BCM_RELAY_CH4_SPARE = 16
BCM_RELAY_CH5_SPARE = 19
BCM_RELAY_CH6_SPARE = 20

# Buzzer output
BCM_BUZZER = 12


class RelayChannel(Enum):
    """Relay channels with their fixed BCM GPIO assignments."""
    CH1_FAN       = BCM_RELAY_CH1_FAN
    CH2_MIST      = BCM_RELAY_CH2_MIST
    CH3_LIGHT     = BCM_RELAY_CH3_LIGHT
    CH4_SPARE     = BCM_RELAY_CH4_SPARE
    CH5_SPARE     = BCM_RELAY_CH5_SPARE
    CH6_SPARE     = BCM_RELAY_CH6_SPARE


@dataclass(frozen=True)
class RelayChannelInfo:
    """Static metadata for a relay channel."""
    channel: RelayChannel
    bcm_gpio: int
    physical_pin: int
    load_name: str
    is_spare: bool


RELAY_CHANNEL_INFO: dict[RelayChannel, RelayChannelInfo] = {
    RelayChannel.CH1_FAN:   RelayChannelInfo(
        RelayChannel.CH1_FAN,   BCM_RELAY_CH1_FAN,   29, "Fan",              False),
    RelayChannel.CH2_MIST:  RelayChannelInfo(
        RelayChannel.CH2_MIST,  BCM_RELAY_CH2_MIST,  31, "Mist sprayer/pump", False),
    RelayChannel.CH3_LIGHT: RelayChannelInfo(
        RelayChannel.CH3_LIGHT, BCM_RELAY_CH3_LIGHT, 33, "Night light",       False),
    RelayChannel.CH4_SPARE: RelayChannelInfo(
        RelayChannel.CH4_SPARE, BCM_RELAY_CH4_SPARE, 36, "Spare CH4",         True),
    RelayChannel.CH5_SPARE: RelayChannelInfo(
        RelayChannel.CH5_SPARE, BCM_RELAY_CH5_SPARE, 35, "Spare CH5",         True),
    RelayChannel.CH6_SPARE: RelayChannelInfo(
        RelayChannel.CH6_SPARE, BCM_RELAY_CH6_SPARE, 38, "Spare CH6",         True),
}

# All relay BCM pins as a frozen set for safety validation
ALL_RELAY_BCM_PINS: frozenset[int] = frozenset(
    ch.value for ch in RelayChannel
)

# All output BCM pins (relays + buzzer)
ALL_OUTPUT_BCM_PINS: frozenset[int] = ALL_RELAY_BCM_PINS | {BCM_BUZZER}

# All input BCM pins
ALL_INPUT_BCM_PINS: frozenset[int] = frozenset({
    BCM_FLOAT_SWITCH,
    BCM_MAINS_DETECTOR,
})


def get_relay_info(channel: RelayChannel) -> RelayChannelInfo:
    """Return static info for a relay channel."""
    return RELAY_CHANNEL_INFO[channel]


def validate_no_pin_conflicts() -> list[str]:
    """Check that no BCM pin is assigned to more than one function.

    Returns a list of conflict descriptions (empty if valid).
    """
    seen: dict[int, str] = {}
    conflicts: list[str] = []
    for entry in PIN_MAP:
        if entry.bcm_gpio is not None:
            if entry.bcm_gpio in seen:
                conflicts.append(
                    f"BCM{entry.bcm_gpio} assigned to both "
                    f"'{seen[entry.bcm_gpio]}' and '{entry.assignment}'"
                )
            else:
                seen[entry.bcm_gpio] = entry.assignment
    return conflicts


def format_pin_table() -> str:
    """Return a human-readable pin map table for logging/diagnostics."""
    lines = [
        f"{'Phys':>4}  {'BCM':>4}  {'Dir':<10}  {'Assignment':<40}  Notes",
        "-" * 90,
    ]
    for entry in sorted(PIN_MAP, key=lambda e: e.physical_pin):
        bcm = f"{entry.bcm_gpio}" if entry.bcm_gpio is not None else "---"
        lines.append(
            f"{entry.physical_pin:>4}  {bcm:>4}  "
            f"{entry.direction.value:<10}  {entry.assignment:<40}  {entry.notes}"
        )
    return "\n".join(lines)
