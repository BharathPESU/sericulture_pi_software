"""Integration tests for the BootSupervisor and safe boot sequences."""

import pytest
from sericulture.boot_supervisor import BootSupervisor
from sericulture.config import load_config
from sericulture.diagnostics import (
    ConfigChecker,
    DiagnosticsRunner,
    DiskSpaceChecker,
    GpioBackendChecker,
    HailoModelChecker,
)
from sericulture.event_types import (
    ActuatorCommand,
    DiagnosticResult,
    DiagnosticStatus,
    FaultCategory,
    SystemState,
)
from sericulture.hardware.buzzer import BuzzerController
from sericulture.hardware.camera_uvc import MockCameraBackend, UVCCamera
from sericulture.hardware.float_switch import FloatSwitchMonitor, MockDigitalInputDriver
from sericulture.hardware.mains_monitor import MainsMonitor, MockMainsInputDriver
from sericulture.hardware.pin_map import RelayChannel
from sericulture.hardware.relay_manager import MockGPIOFactory, RelayManager
from sericulture.hardware.sht40 import MockI2CDriver, SHT40Sensor
from sericulture.state_machine import StateMachine
from sericulture.storage.database import DatabaseManager
from sericulture.storage.event_repository import EventRepository
from sericulture.vision.hailo_inference import HailoInferenceManager, MockHailoBackend


@pytest.fixture
def boot_env(tmp_path):
    cfg = load_config("config/device.example.yaml")
    cfg_storage = cfg.storage.__class__(db_path=str(tmp_path / "boot_test.db"), min_free_disk_mb=50)
    cfg = cfg.__class__(
        device=cfg.device,
        relay=cfg.relay,
        float_switch=cfg.float_switch,
        mains_detector=cfg.mains_detector,
        buzzer=cfg.buzzer,
        sht40=cfg.sht40,
        camera=cfg.camera,
        hailo=cfg.hailo,
        tracking=cfg.tracking,
        movement=cfg.movement,
        control=cfg.control,
        storage=cfg_storage,
        cloud=cfg.cloud,
        watchdog=cfg.watchdog,
        optional=cfg.optional,
        log_level=cfg.log_level,
    )

    sm = StateMachine()
    gpio_factory = MockGPIOFactory()
    relays = RelayManager(cfg.relay, gpio_factory=gpio_factory)
    buzzer = BuzzerController(cfg.buzzer, gpio_factory=gpio_factory)
    db = DatabaseManager(cfg.storage)
    repo = EventRepository(db, device_id=cfg.device.device_id, farm_id=cfg.device.farm_id)

    # Hardware with mock drivers
    sht_driver = MockI2CDriver()
    sht40 = SHT40Sensor(cfg.sht40, driver=sht_driver)
    float_sw = FloatSwitchMonitor(cfg.float_switch, driver=MockDigitalInputDriver(0))
    mains = MainsMonitor(cfg.mains_detector, driver=MockMainsInputDriver(1))
    camera = UVCCamera(cfg.camera, backend=MockCameraBackend())
    hailo = HailoInferenceManager(cfg.hailo, backend=MockHailoBackend())

    diag = DiagnosticsRunner(cfg, boot_id=sm.boot_id)
    diag.register(ConfigChecker(cfg))
    diag.register(GpioBackendChecker(relays))
    diag.register(relays)
    diag.register(buzzer)
    diag.register(db)
    diag.register(DiskSpaceChecker(db))
    diag.register(sht40)
    diag.register(float_sw)
    diag.register(mains)
    diag.register(camera)
    diag.register(hailo)
    diag.register(HailoModelChecker(hailo))

    supervisor = BootSupervisor(
        config=cfg,
        state_machine=sm,
        relay_manager=relays,
        buzzer=buzzer,
        db_manager=db,
        diagnostics_runner=diag,
        event_repo=repo,
    )

    return supervisor, sm, relays, sht_driver


def test_boot_sequence_all_healthy_enters_auto_ready(boot_env):
    supervisor, sm, relays, sht_driver = boot_env
    success = supervisor.run_boot_sequence(auto_ready=True)

    assert success is True
    assert sm.current_state == SystemState.AUTO_READY
    # Relays must still be safe OFF
    assert relays.get_channel_state(RelayChannel.CH1_FAN) == ActuatorCommand.OFF


def test_boot_sequence_component_failure_enters_lockout(boot_env):
    supervisor, sm, relays, sht_driver = boot_env
    # Inject failure: SHT40 fails probe
    sht_driver.probe_result = False

    success = supervisor.run_boot_sequence(auto_ready=True)

    assert success is False
    assert sm.current_state == SystemState.FAULT_LOCKOUT
    assert relays.get_channel_state(RelayChannel.CH1_FAN) == ActuatorCommand.OFF
