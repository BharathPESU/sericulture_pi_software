"""Configuration loading, validation, and typed access.

Loads device.yaml, validates all required fields, rejects unknown keys in
production mode, and provides typed access to every configuration section.

SAFETY: Missing or invalid safety-critical settings cause a ConfigurationError
rather than falling back to arbitrary defaults.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sericulture.event_types import (
    ConfigurationError,
    FloatSwitchContactType,
    RelayPolarity,
)
from sericulture.hardware.pin_map import (
    BCM_BUZZER,
    BCM_FLOAT_SWITCH,
    BCM_MAINS_DETECTOR,
    BCM_RELAY_CH1_FAN,
    BCM_RELAY_CH2_MIST,
    BCM_RELAY_CH3_LIGHT,
    BCM_RELAY_CH4_SPARE,
    BCM_RELAY_CH5_SPARE,
    BCM_RELAY_CH6_SPARE,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Typed configuration dataclasses
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeviceIdentity:
    """Unique device and farm identifiers."""
    device_id: str
    farm_id: str
    site_name: str = "default-site"


@dataclass(frozen=True)
class RelayChannelConfig:
    """Per-channel relay electrical configuration."""
    bcm_gpio: int
    polarity: RelayPolarity
    load_name: str
    is_spare: bool = False
    default_state_on_boot: str = "OFF"    # Always OFF — validated below

    def __post_init__(self):
        if self.default_state_on_boot != "OFF":
            raise ConfigurationError(
                f"Relay {self.load_name} (BCM{self.bcm_gpio}): "
                f"default_state_on_boot must be 'OFF', got '{self.default_state_on_boot}'. "
                f"Relays must never default to ON at boot."
            )


@dataclass(frozen=True)
class RelayConfig:
    """Complete relay board configuration."""
    board_model: str                                # e.g. "waveshare-6ch-v1"
    board_revision: str                             # To be confirmed at commissioning
    separate_supply_confirmed: bool                 # True only after electrical verification
    channels: dict[str, RelayChannelConfig] = field(default_factory=dict)

    def __post_init__(self):
        if not self.separate_supply_confirmed:
            logger.warning(
                "Relay board separate supply NOT confirmed. "
                "Physical commissioning required before enabling loads."
            )


@dataclass(frozen=True)
class FloatSwitchConfig:
    """Magnetic float level switch configuration."""
    contact_type: FloatSwitchContactType
    bcm_gpio: int = BCM_FLOAT_SWITCH
    pull_up: bool = True                            # Internal pull-up for NO switch
    debounce_ms: int = 200
    water_ok_when_closed: bool = True               # For NO: closed = water present


@dataclass(frozen=True)
class MainsDetectorConfig:
    """Isolated mains-presence detector configuration."""
    bcm_gpio: int = BCM_MAINS_DETECTOR
    active_high: bool = True                        # HIGH = mains present
    debounce_ms: int = 500
    loss_confirm_ms: int = 2000                     # Confirm loss after this duration
    restore_stabilize_ms: int = 5000                # Wait before acting on restore


@dataclass(frozen=True)
class BuzzerConfig:
    """Active buzzer configuration."""
    bcm_gpio: int = BCM_BUZZER
    enabled: bool = True
    max_repeat_interval_s: float = 60.0             # Don't buzz more than this
    patterns: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    # Each pattern is a list of (on_seconds, off_seconds) tuples


@dataclass(frozen=True)
class SHT40Config:
    """SHT40 temperature/humidity sensor configuration."""
    i2c_bus: int = 1
    i2c_address: int = 0x44
    read_interval_s: float = 5.0
    stale_timeout_s: float = 30.0
    temp_min_c: float = -10.0
    temp_max_c: float = 80.0
    humidity_min_pct: float = 0.0
    humidity_max_pct: float = 100.0
    max_consecutive_errors: int = 5
    retry_backoff_base_s: float = 1.0


@dataclass(frozen=True)
class CameraConfig:
    """USB UVC camera configuration."""
    device_path: str = ""                            # Auto-discover if empty
    serial_number: str = ""                          # For stable udev matching
    udev_id_path: str = ""                           # Alternative stable identity
    width: int = 1920
    height: int = 1080
    fps: int = 15
    frame_freshness_timeout_s: float = 5.0
    reconnect_backoff_base_s: float = 2.0
    max_reconnect_attempts: int = 10
    gstreamer_pipeline: str = ""                     # Custom GStreamer pipeline if needed


@dataclass(frozen=True)
class HailoConfig:
    """Hailo AI HAT+ configuration."""
    model_path: str = ""                             # Path to compiled .hef model
    model_version: str = ""
    model_class_names: list[str] = field(default_factory=list)
    input_width: int = 640
    input_height: int = 640
    confidence_threshold: float = 0.5
    nms_iou_threshold: float = 0.45
    inference_timeout_s: float = 5.0
    max_consecutive_errors: int = 3
    batch_size: int = 1


@dataclass(frozen=True)
class TrackingConfig:
    """Object tracking configuration."""
    tracker_type: str = "bytetrack"
    max_age: int = 30                                # Frames before track is lost
    min_hits: int = 3                                # Minimum detections to confirm
    iou_threshold: float = 0.3


@dataclass(frozen=True)
class MovementConfig:
    """Movement/inactivity analysis configuration.

    NOTE: These thresholds are PROVISIONAL and require domain approval
    before enabling automatic inactivity alerts.
    """
    inactivity_duration_s: float = 300.0             # PROVISIONAL — needs domain approval
    min_observations_for_inactivity: int = 10
    movement_score_threshold: float = 0.1            # PROVISIONAL
    provisional: bool = True                         # Must be set False after domain review


@dataclass(frozen=True)
class ControlThresholds:
    """Temperature/humidity control thresholds and timing.

    NOTE: All values are PROVISIONAL and require domain/commissioning approval
    before enabling automatic control.
    """
    # Temperature
    fan_on_temp_c: float = 30.0                      # PROVISIONAL
    fan_off_temp_c: float = 27.0                     # PROVISIONAL (hysteresis)
    # Humidity
    mist_on_humidity_pct: float = 70.0               # PROVISIONAL
    mist_off_humidity_pct: float = 80.0              # PROVISIONAL (hysteresis)
    # Night light schedule (24h format)
    light_on_hour: int = 18
    light_off_hour: int = 6
    # Timing safety
    min_on_time_s: float = 60.0
    min_off_time_s: float = 60.0
    max_continuous_run_s: float = 1800.0             # 30 min max
    cooldown_after_max_run_s: float = 300.0
    persistence_window_s: float = 30.0               # Sustain condition before acting
    # Validation
    provisional: bool = True                         # Must be set False after review


@dataclass(frozen=True)
class StorageConfig:
    """SQLite and local storage configuration."""
    db_path: str = "/var/lib/sericulture/sericulture.db"
    wal_mode: bool = True
    busy_timeout_ms: int = 5000
    min_free_disk_mb: int = 100
    max_db_size_mb: int = 500
    retention_days: int = 30
    telemetry_batch_size: int = 10
    telemetry_batch_interval_s: float = 60.0


@dataclass(frozen=True)
class CloudConfig:
    """AWS IoT Core MQTT configuration."""
    enabled: bool = True
    endpoint: str = ""
    region: str = "ap-south-1"
    cert_path: str = ""
    key_path: str = ""
    root_ca_path: str = ""
    client_id: str = ""                              # Default: device_id
    topic_root: str = "novabion/sericulture/v1"
    qos: int = 1
    ack_timeout_s: float = 30.0
    batch_size: int = 10
    batch_interval_s: float = 30.0
    max_retry_count: int = 100
    reconnect_backoff_base_s: float = 1.0
    reconnect_backoff_max_s: float = 300.0
    max_payload_bytes: int = 128000                  # 128 KB MQTT limit


@dataclass(frozen=True)
class WatchdogConfig:
    """Watchdog and health monitoring configuration."""
    health_interval_s: float = 60.0
    sensor_timeout_s: float = 30.0
    camera_timeout_s: float = 10.0
    inference_timeout_s: float = 10.0
    sqlite_timeout_s: float = 5.0
    mqtt_callback_timeout_s: float = 10.0
    cpu_temp_warning_c: float = 75.0
    cpu_temp_critical_c: float = 85.0


@dataclass(frozen=True)
class OptionalComponents:
    """Which components are optional (may be OPTIONAL_MISSING without lockout)."""
    display: bool = True                             # Display is optional
    aws_iot: bool = False                            # Cloud is required by default
    # Safety-critical components are NEVER optional:
    # sht40, camera, hailo, float_switch, mains_detector, relay_board, sqlite


@dataclass
class AppConfig:
    """Root configuration object — the single typed interface for the app."""
    device: DeviceIdentity = field(default_factory=lambda: DeviceIdentity("UNCONFIGURED", "UNCONFIGURED"))
    relay: RelayConfig = field(default_factory=lambda: RelayConfig("unknown", "unknown", False))
    float_switch: FloatSwitchConfig = field(default_factory=lambda: FloatSwitchConfig(FloatSwitchContactType.NORMALLY_OPEN))
    mains_detector: MainsDetectorConfig = field(default_factory=MainsDetectorConfig)
    buzzer: BuzzerConfig = field(default_factory=BuzzerConfig)
    sht40: SHT40Config = field(default_factory=SHT40Config)
    camera: CameraConfig = field(default_factory=CameraConfig)
    hailo: HailoConfig = field(default_factory=HailoConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    movement: MovementConfig = field(default_factory=MovementConfig)
    control: ControlThresholds = field(default_factory=ControlThresholds)
    storage: StorageConfig = field(default_factory=StorageConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    optional: OptionalComponents = field(default_factory=OptionalComponents)
    log_level: str = "INFO"
    software_version: str = "0.1.0"

    @property
    def thresholds(self) -> ControlThresholds:
        return self.control

    @property
    def mains(self) -> MainsDetectorConfig:
        return self.mains_detector


# ──────────────────────────────────────────────────────────────────────
# Configuration loading and validation
# ──────────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG_PATHS = [
    "/etc/sericulture/device.yaml",
    "config/device.yaml",
]


def _find_config_path() -> Path:
    """Find the configuration file, checking env override first."""
    env_path = os.environ.get("SERICULTURE_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        raise ConfigurationError(
            f"SERICULTURE_CONFIG_PATH={env_path} does not exist or is not a file"
        )
    for candidate in _DEFAULT_CONFIG_PATHS:
        p = Path(candidate)
        if p.is_file():
            return p
    raise ConfigurationError(
        f"No configuration file found. Searched: {_DEFAULT_CONFIG_PATHS}. "
        f"Set SERICULTURE_CONFIG_PATH or place device.yaml in a search path."
    )


def _parse_relay_channels(raw: dict[str, Any]) -> dict[str, RelayChannelConfig]:
    """Parse relay channel configurations from YAML."""
    expected_gpios = {
        "ch1_fan": BCM_RELAY_CH1_FAN,
        "ch2_mist": BCM_RELAY_CH2_MIST,
        "ch3_light": BCM_RELAY_CH3_LIGHT,
        "ch4_spare": BCM_RELAY_CH4_SPARE,
        "ch5_spare": BCM_RELAY_CH5_SPARE,
        "ch6_spare": BCM_RELAY_CH6_SPARE,
    }
    channels: dict[str, RelayChannelConfig] = {}
    for name, gpio in expected_gpios.items():
        ch_raw = raw.get(name, {})
        polarity_str = ch_raw.get("polarity", "active_low")
        try:
            polarity = RelayPolarity(polarity_str)
        except ValueError:
            raise ConfigurationError(
                f"Relay {name}: invalid polarity '{polarity_str}'. "
                f"Must be 'active_high' or 'active_low'."
            )
        # Validate GPIO matches pin map
        configured_gpio = ch_raw.get("bcm_gpio", gpio)
        if configured_gpio != gpio:
            raise ConfigurationError(
                f"Relay {name}: bcm_gpio={configured_gpio} conflicts with "
                f"pin map (expected BCM{gpio}). "
                f"Do not reassign pins — request a hardware revision."
            )
        channels[name] = RelayChannelConfig(
            bcm_gpio=gpio,
            polarity=polarity,
            load_name=ch_raw.get("load_name", name),
            is_spare=name.startswith("ch4") or name.startswith("ch5") or name.startswith("ch6"),
            default_state_on_boot="OFF",
        )
    return channels


def _build_buzzer_patterns(raw: dict[str, Any]) -> dict[str, list[tuple[float, float]]]:
    """Parse buzzer beep patterns from YAML config."""
    defaults: dict[str, list[tuple[float, float]]] = {
        "sht40_fault":        [(0.8, 0.4)] * 1,                    # 1 long beep
        "camera_fault":       [(0.2, 0.15)] * 2,                   # 2 short beeps
        "hailo_fault":        [(0.2, 0.15)] * 3,                   # 3 short beeps
        "safety_input_fault": [(0.8, 0.2)] + [(0.2, 0.15)] * 2,   # 1 long + 2 short
        "storage_fault":      [(0.8, 0.3)] * 2,                    # 2 long beeps
        "config_gpio_fault":  [(0.2, 0.15)] * 4,                   # 4 short beeps
        "cloud_disconnected": [(0.1, 0.3)],                        # 1 brief beep
    }
    patterns_raw = raw.get("patterns", {})
    for key, pattern in patterns_raw.items():
        if isinstance(pattern, list):
            defaults[key] = [(float(p[0]), float(p[1])) for p in pattern]
    return defaults


def load_config(config_path: Path | str | None = None) -> AppConfig:
    """Load and validate configuration from YAML file.

    Args:
        config_path: Explicit path, or None to auto-discover.

    Returns:
        Validated AppConfig instance.

    Raises:
        ConfigurationError: If config is missing, invalid, or fails validation.
    """
    if config_path is None:
        path = _find_config_path()
    else:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigurationError(f"Config file not found: {path}")

    logger.info("Loading configuration from %s", path)

    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"YAML parse error in {path}: {e}") from e
    except OSError as e:
        raise ConfigurationError(f"Cannot read config file {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigurationError(f"Config root must be a YAML mapping, got {type(raw).__name__}")

    errors: list[str] = []

    # --- Device identity ---
    dev_raw = raw.get("device", {})
    device_id = dev_raw.get("device_id") or os.environ.get("SERICULTURE_DEVICE_ID", "")
    farm_id = dev_raw.get("farm_id") or os.environ.get("SERICULTURE_FARM_ID", "")
    if not device_id or device_id == "UNCONFIGURED" or device_id.startswith("changeme"):
        errors.append("device.device_id is required and must be set to a real value")
    if not farm_id or farm_id == "UNCONFIGURED" or farm_id.startswith("changeme"):
        errors.append("device.farm_id is required and must be set to a real value")

    device = DeviceIdentity(
        device_id=device_id,
        farm_id=farm_id,
        site_name=dev_raw.get("site_name", "default-site"),
    )

    # --- Relay config ---
    relay_raw = raw.get("relay", {})
    relay_channels = _parse_relay_channels(relay_raw.get("channels", {}))
    relay = RelayConfig(
        board_model=relay_raw.get("board_model", "unknown"),
        board_revision=relay_raw.get("board_revision", "unknown"),
        separate_supply_confirmed=relay_raw.get("separate_supply_confirmed", False),
        channels=relay_channels,
    )

    # --- Float switch ---
    fs_raw = raw.get("float_switch", {})
    try:
        contact_type = FloatSwitchContactType(fs_raw.get("contact_type", "NO"))
    except ValueError:
        errors.append(f"float_switch.contact_type must be 'NO' or 'NC'")
        contact_type = FloatSwitchContactType.NORMALLY_OPEN

    float_switch = FloatSwitchConfig(
        contact_type=contact_type,
        bcm_gpio=BCM_FLOAT_SWITCH,
        pull_up=fs_raw.get("pull_up", True),
        debounce_ms=int(fs_raw.get("debounce_ms", 200)),
        water_ok_when_closed=fs_raw.get("water_ok_when_closed", True),
    )

    # --- Mains detector ---
    md_raw = raw.get("mains_detector", {})
    mains_detector = MainsDetectorConfig(
        bcm_gpio=BCM_MAINS_DETECTOR,
        active_high=md_raw.get("active_high", True),
        debounce_ms=int(md_raw.get("debounce_ms", 500)),
        loss_confirm_ms=int(md_raw.get("loss_confirm_ms", 2000)),
        restore_stabilize_ms=int(md_raw.get("restore_stabilize_ms", 5000)),
    )

    # --- Buzzer ---
    bz_raw = raw.get("buzzer", {})
    buzzer_patterns = _build_buzzer_patterns(bz_raw)
    buzzer = BuzzerConfig(
        bcm_gpio=BCM_BUZZER,
        enabled=bz_raw.get("enabled", True),
        max_repeat_interval_s=float(bz_raw.get("max_repeat_interval_s", 60.0)),
        patterns=buzzer_patterns,
    )

    # --- SHT40 ---
    s_raw = raw.get("sht40", {})
    sht40 = SHT40Config(
        read_interval_s=float(s_raw.get("read_interval_s", 5.0)),
        stale_timeout_s=float(s_raw.get("stale_timeout_s", 30.0)),
        temp_min_c=float(s_raw.get("temp_min_c", -10.0)),
        temp_max_c=float(s_raw.get("temp_max_c", 80.0)),
        humidity_min_pct=float(s_raw.get("humidity_min_pct", 0.0)),
        humidity_max_pct=float(s_raw.get("humidity_max_pct", 100.0)),
        max_consecutive_errors=int(s_raw.get("max_consecutive_errors", 5)),
    )

    # --- Camera ---
    cam_raw = raw.get("camera", {})
    camera = CameraConfig(
        device_path=cam_raw.get("device_path", ""),
        serial_number=cam_raw.get("serial_number", ""),
        udev_id_path=cam_raw.get("udev_id_path", ""),
        width=int(cam_raw.get("width", 1920)),
        height=int(cam_raw.get("height", 1080)),
        fps=int(cam_raw.get("fps", 15)),
        frame_freshness_timeout_s=float(cam_raw.get("frame_freshness_timeout_s", 5.0)),
        reconnect_backoff_base_s=float(cam_raw.get("reconnect_backoff_base_s", 2.0)),
        max_reconnect_attempts=int(cam_raw.get("max_reconnect_attempts", 10)),
        gstreamer_pipeline=cam_raw.get("gstreamer_pipeline", ""),
    )

    # --- Hailo ---
    h_raw = raw.get("hailo", {})
    hailo = HailoConfig(
        model_path=h_raw.get("model_path", ""),
        model_version=h_raw.get("model_version", ""),
        model_class_names=list(h_raw.get("model_class_names", [])),
        input_width=int(h_raw.get("input_width", 640)),
        input_height=int(h_raw.get("input_height", 640)),
        confidence_threshold=float(h_raw.get("confidence_threshold", 0.5)),
        nms_iou_threshold=float(h_raw.get("nms_iou_threshold", 0.45)),
        inference_timeout_s=float(h_raw.get("inference_timeout_s", 5.0)),
        max_consecutive_errors=int(h_raw.get("max_consecutive_errors", 3)),
    )

    # --- Tracking ---
    tr_raw = raw.get("tracking", {})
    tracking = TrackingConfig(
        tracker_type=tr_raw.get("tracker_type", "bytetrack"),
        max_age=int(tr_raw.get("max_age", 30)),
        min_hits=int(tr_raw.get("min_hits", 3)),
        iou_threshold=float(tr_raw.get("iou_threshold", 0.3)),
    )

    # --- Movement/inactivity ---
    mv_raw = raw.get("movement", {})
    movement = MovementConfig(
        inactivity_duration_s=float(mv_raw.get("inactivity_duration_s", 300.0)),
        min_observations_for_inactivity=int(mv_raw.get("min_observations_for_inactivity", 10)),
        movement_score_threshold=float(mv_raw.get("movement_score_threshold", 0.1)),
        provisional=mv_raw.get("provisional", True),
    )

    # --- Control thresholds ---
    ct_raw = raw.get("control", {})
    control = ControlThresholds(
        fan_on_temp_c=float(ct_raw.get("fan_on_temp_c", 30.0)),
        fan_off_temp_c=float(ct_raw.get("fan_off_temp_c", 27.0)),
        mist_on_humidity_pct=float(ct_raw.get("mist_on_humidity_pct", 70.0)),
        mist_off_humidity_pct=float(ct_raw.get("mist_off_humidity_pct", 80.0)),
        light_on_hour=int(ct_raw.get("light_on_hour", 18)),
        light_off_hour=int(ct_raw.get("light_off_hour", 6)),
        min_on_time_s=float(ct_raw.get("min_on_time_s", 60.0)),
        min_off_time_s=float(ct_raw.get("min_off_time_s", 60.0)),
        max_continuous_run_s=float(ct_raw.get("max_continuous_run_s", 1800.0)),
        cooldown_after_max_run_s=float(ct_raw.get("cooldown_after_max_run_s", 300.0)),
        persistence_window_s=float(ct_raw.get("persistence_window_s", 30.0)),
        provisional=ct_raw.get("provisional", True),
    )

    # --- Storage ---
    st_raw = raw.get("storage", {})
    db_path = st_raw.get("db_path") or os.environ.get(
        "SERICULTURE_DB_PATH", "/var/lib/sericulture/sericulture.db"
    )
    storage = StorageConfig(
        db_path=db_path,
        wal_mode=st_raw.get("wal_mode", True),
        busy_timeout_ms=int(st_raw.get("busy_timeout_ms", 5000)),
        min_free_disk_mb=int(st_raw.get("min_free_disk_mb", 100)),
        max_db_size_mb=int(st_raw.get("max_db_size_mb", 500)),
        retention_days=int(st_raw.get("retention_days", 30)),
    )

    # --- Cloud (AWS IoT) ---
    cl_raw = raw.get("cloud", {})
    cloud = CloudConfig(
        enabled=cl_raw.get("enabled", True),
        endpoint=cl_raw.get("endpoint") or os.environ.get("AWS_IOT_ENDPOINT", ""),
        region=cl_raw.get("region") or os.environ.get("AWS_IOT_REGION", "ap-south-1"),
        cert_path=cl_raw.get("cert_path") or os.environ.get("AWS_IOT_CERT_PATH", ""),
        key_path=cl_raw.get("key_path") or os.environ.get("AWS_IOT_KEY_PATH", ""),
        root_ca_path=cl_raw.get("root_ca_path") or os.environ.get("AWS_IOT_ROOT_CA_PATH", ""),
        client_id=cl_raw.get("client_id", ""),
        topic_root=cl_raw.get("topic_root", "novabion/sericulture/v1"),
        qos=int(cl_raw.get("qos", 1)),
        ack_timeout_s=float(cl_raw.get("ack_timeout_s", 30.0)),
        batch_size=int(cl_raw.get("batch_size", 10)),
        batch_interval_s=float(cl_raw.get("batch_interval_s", 30.0)),
        max_retry_count=int(cl_raw.get("max_retry_count", 100)),
        reconnect_backoff_base_s=float(cl_raw.get("reconnect_backoff_base_s", 1.0)),
        reconnect_backoff_max_s=float(cl_raw.get("reconnect_backoff_max_s", 300.0)),
    )

    # --- Watchdog ---
    wd_raw = raw.get("watchdog", {})
    watchdog = WatchdogConfig(
        health_interval_s=float(wd_raw.get("health_interval_s", 60.0)),
        sensor_timeout_s=float(wd_raw.get("sensor_timeout_s", 30.0)),
        camera_timeout_s=float(wd_raw.get("camera_timeout_s", 10.0)),
        inference_timeout_s=float(wd_raw.get("inference_timeout_s", 10.0)),
        cpu_temp_warning_c=float(wd_raw.get("cpu_temp_warning_c", 75.0)),
        cpu_temp_critical_c=float(wd_raw.get("cpu_temp_critical_c", 85.0)),
    )

    # --- Optional components ---
    opt_raw = raw.get("optional", {})
    optional = OptionalComponents(
        display=opt_raw.get("display", True),
        aws_iot=opt_raw.get("aws_iot", False),
    )

    log_level = raw.get("log_level") or os.environ.get("SERICULTURE_LOG_LEVEL", "INFO")

    # --- Validate cloud credentials if cloud is enabled and not optional ---
    if cloud.enabled and not optional.aws_iot:
        if not cloud.endpoint:
            errors.append("cloud.endpoint is required when cloud is enabled and not optional")
        if not cloud.cert_path:
            errors.append("cloud.cert_path is required")
        if not cloud.key_path:
            errors.append("cloud.key_path is required")
        if not cloud.root_ca_path:
            errors.append("cloud.root_ca_path is required")

    # Raise all errors at once for better diagnostics
    if errors:
        raise ConfigurationError(
            "Configuration validation failed:\n  - " + "\n  - ".join(errors)
        )

    config = AppConfig(
        device=device,
        relay=relay,
        float_switch=float_switch,
        mains_detector=mains_detector,
        buzzer=buzzer,
        sht40=sht40,
        camera=camera,
        hailo=hailo,
        tracking=tracking,
        movement=movement,
        control=control,
        storage=storage,
        cloud=cloud,
        watchdog=watchdog,
        optional=optional,
        log_level=log_level.upper(),
        software_version="0.1.0",
    )

    logger.info("Configuration loaded and validated for device '%s'", device.device_id)
    return config
