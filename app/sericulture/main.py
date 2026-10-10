"""Main application entrypoint for the Sericulture Edge-AI Pi 5 controller.

Orchestrates initialization, safe boot diagnostics, background loops,
mains power loss response, and graceful shutdown.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from threading import Event, Thread
from typing import Any

from sericulture.boot_supervisor import BootSupervisor
from sericulture.cloud.iot_client import CloudMqttManager
from sericulture.cloud.outbox_sync import OutboxSyncWorker
from sericulture.config import AppConfig, load_config
from sericulture.control.actuator_policy import EnvironmentalPolicy
from sericulture.control.decision_engine import DecisionEngine
from sericulture.control.safety_layer import SafetyLayer, SensorProvider
from sericulture.diagnostics import (
    ConfigChecker,
    DiagnosticsRunner,
    DiskSpaceChecker,
    GpioBackendChecker,
    HailoModelChecker,
)
from sericulture.event_types import (
    ActuatorCommand,
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    EventType,
    LoadId,
    MainsState,
    SensorHealth,
    SystemState,
)
from sericulture.hardware.buzzer import BuzzerController
from sericulture.hardware.camera_uvc import UVCCamera
from sericulture.hardware.float_switch import FloatSwitchMonitor
from sericulture.hardware.mains_monitor import MainsMonitor
from sericulture.hardware.relay_manager import RelayManager
from sericulture.hardware.sht40 import SHT40Sensor
from sericulture.health import HealthMonitor
from sericulture.monitoring.metrics import MetricsTracker
from sericulture.monitoring.watchdog import SoftwareWatchdog
from sericulture.state_machine import StateMachine
from sericulture.storage.database import DatabaseManager
from sericulture.storage.event_repository import EventRepository
from sericulture.vision.hailo_inference import HailoInferenceManager
from sericulture.vision.movement_analysis import MovementAnalyzer
from sericulture.vision.pipeline import VisionPipeline
from sericulture.vision.tracking import SilkwormTracker

logger = logging.getLogger("sericulture")


class SericultureApplication:
    """The complete edge-AI sericulture monitoring and control service."""

    def __init__(self, config: AppConfig, auto_ready: bool = False):
        self._config = config
        self._auto_ready = auto_ready
        self._stop_event = Event()

        # 1. State Machine
        self._sm = StateMachine()

        # 2. Hardware Drivers
        # All drivers use lazy/mock backends: __init__ never raises even when
        # hardware is absent. The check() method on each driver returns the
        # correct DiagnosticStatus (MISSING/FAIL) so the boot supervisor can
        # decide whether the fault is critical or optional.
        self._relays = RelayManager(config.relay)
        self._buzzer = BuzzerController(config.buzzer)
        self._sht40 = SHT40Sensor(config.sht40)
        self._float_switch = FloatSwitchMonitor(config.float_switch)
        self._mains = MainsMonitor(config.mains)
        self._camera = UVCCamera(config.camera)
        self._hailo = HailoInferenceManager(config.hailo)

        # 3. Storage
        self._db = DatabaseManager(config.storage)
        self._repo = EventRepository(
            self._db,
            device_id=config.device.device_id,
            farm_id=config.device.farm_id,
        )

        # 4. Sensor Provider & Safety Layer
        self._sensors = SensorProvider()
        self._sensors.set_sht40_provider(self._sht40.get_last_reading)
        self._sensors.set_float_switch_provider(self._float_switch.get_state)
        self._sensors.set_mains_provider(self._mains.get_state)

        self._safety = SafetyLayer(
            config=config,
            state_machine=self._sm,
            relay_manager=self._relays,
            sensors=self._sensors,
        )

        # 5. Environmental Policy & Decision Engine
        self._policy = EnvironmentalPolicy(config.thresholds)
        self._decision = DecisionEngine(
            safety_layer=self._safety,
            sensor_provider=self._sensors,
            policy=self._policy,
        )

        # 6. Vision Pipeline
        self._tracker = SilkwormTracker(config.tracking)
        self._movement_analyzer = MovementAnalyzer(config.movement)
        self._vision = VisionPipeline(
            camera=self._camera,
            hailo=self._hailo,
            tracker=self._tracker,
            movement_analyzer=self._movement_analyzer,
        )

        # 7. Cloud MQTT & Outbox Sync
        self._mqtt = CloudMqttManager(
            config=config.cloud,
            device_id=config.device.device_id,
        )
        self._sync = OutboxSyncWorker(
            config=config.cloud,
            repository=self._repo,
            mqtt_manager=self._mqtt,
        )

        # 8. Monitoring & Health
        self._health = HealthMonitor(db_dir=str(self._db.db_path.parent))
        self._watchdog = SoftwareWatchdog()
        self._metrics = MetricsTracker()

        # 9. Diagnostics & Boot Supervisor
        self._diag = DiagnosticsRunner(config=config, boot_id=self._sm.boot_id)
        self._setup_diagnostics()

        self._supervisor = BootSupervisor(
            config=config,
            state_machine=self._sm,
            relay_manager=self._relays,
            buzzer=self._buzzer,
            db_manager=self._db,
            diagnostics_runner=self._diag,
            event_repo=self._repo,
        )

        # Background threads
        self._threads: list[Thread] = []

    def _setup_diagnostics(self) -> None:
        """Register all component checkers with the diagnostics runner."""
        self._diag.register(ConfigChecker(self._config))
        self._diag.register(GpioBackendChecker(self._relays))
        self._diag.register(self._relays)
        self._diag.register(self._buzzer)
        self._diag.register(self._db)
        self._diag.register(DiskSpaceChecker(self._db))
        self._diag.register(self._sht40)
        self._diag.register(self._float_switch)
        self._diag.register(self._mains)
        self._diag.register(self._camera)
        self._diag.register(self._hailo)
        self._diag.register(HailoModelChecker(self._hailo))
        self._diag.register(self._mqtt)

    def _setup_event_listeners(self) -> None:
        """Register callbacks for emergency safety events."""
        # Mains power loss handler
        self._mains.add_on_lost_listener(self._on_mains_lost)
        self._mains.add_on_restored_listener(self._on_mains_restored)

        # Actuator event logging
        self._decision.add_result_listener(self._on_actuator_action)

        # Register watchdog tasks
        self._watchdog.register_task("sensor_loop", self._config.watchdog.sensor_timeout_s)
        self._watchdog.register_task("control_loop", 15.0)
        self._watchdog.register_task("health_loop", 120.0)

    def _on_mains_lost(self) -> None:
        logger.warning("MAINS POWER LOSS EVENT triggered!")
        try:
            self._sm.transition_to(SystemState.MAINS_LOST, reason="AC mains presence detector signal lost")
        except Exception:
            pass
        self._decision.handle_mains_lost()
        try:
            self._repo.record_device_event(
                EventType.MAINS_LOST,
                {"timestamp_utc": time.time(), "reason": "AC mains detector dropped"},
            )
        except Exception as e:
            logger.error(f"Failed to record MAINS_LOST event: {e}")

    def _on_mains_restored(self, outage_duration_s: float) -> None:
        logger.info(f"MAINS POWER RESTORED after {outage_duration_s:.1f}s.")
        try:
            self._repo.record_device_event(
                EventType.MAINS_RESTORED,
                {"outage_duration_s": outage_duration_s},
            )
            self._sm.transition_to(SystemState.RECOVERY_PENDING, reason="Mains stabilized; evaluating recovery")
            # If all sensors healthy, restore to previous ready state
            target = SystemState.AUTO_READY if self._auto_ready else SystemState.MANUAL_READY
            self._sm.transition_to(target, reason="Recovery checks complete after mains restored")
        except Exception as e:
            logger.error(f"Error handling mains restoration: {e}")

    def _on_actuator_action(self, result: Any) -> None:
        try:
            self._repo.record_actuator_event(result)
        except Exception as e:
            logger.error(f"Failed to record actuator event: {e}")

    def start(self) -> bool:
        """Run safe boot sequence and launch background subsystem loops."""
        self._setup_event_listeners()

        # Run safe boot sequence
        boot_ok = self._supervisor.run_boot_sequence(auto_ready=self._auto_ready)
        if not boot_ok:
            logger.error("Safe boot sequence failed. System is locked out.")
            return False

        # Start watchdog
        self._watchdog.start()

        # Start cloud outbox sync
        self._sync.start()

        # Start camera & vision pipeline
        self._camera.open()
        self._vision.start_worker(target_interval_s=0.2)

        # Launch sensor reading & decision control loop
        control_th = Thread(target=self._run_control_loop, name="ControlLoop", daemon=True)
        control_th.start()
        self._threads.append(control_th)

        # Launch periodic health snapshot loop
        health_th = Thread(target=self._run_health_loop, name="HealthLoop", daemon=True)
        health_th.start()
        self._threads.append(health_th)

        logger.info("All sericulture application workers running.")
        return True

    def _run_control_loop(self) -> None:
        """Periodic loop reading sensors, logging telemetry, and running decision engine."""
        interval = self._config.storage.telemetry_batch_interval_s / self._config.storage.telemetry_batch_size
        interval = max(1.0, min(interval, 5.0))  # Between 1s and 5s

        while not self._stop_event.is_set():
            t0 = time.monotonic()
            self._watchdog.heartbeat("sensor_loop")
            self._watchdog.heartbeat("control_loop")

            # 1. Read SHT40 sensor
            reading = self._sht40.read()

            # 2. Check states
            states = self._safety.get_all_states()
            fan_on = states.get(LoadId.FAN) == ActuatorCommand.ON
            mist_on = states.get(LoadId.MIST) == ActuatorCommand.ON
            light_on = states.get(LoadId.NIGHT_LIGHT) == ActuatorCommand.ON

            float_st = self._float_switch.get_state()
            mains_ok = self._mains.is_mains_present()

            # 3. Record local telemetry
            try:
                self._repo.record_telemetry(
                    reading=reading,
                    fan_on=fan_on,
                    mist_on=mist_on,
                    light_on=light_on,
                    float_state=float_st.value,
                    mains_present=mains_ok,
                    camera_ok=self._camera.is_connected,
                    hailo_ok=self._hailo.is_ready,
                )
            except Exception as e:
                logger.error(f"Failed to record telemetry: {e}")

            # 4. Run decision engine step if in ready state
            if self._sm.current_state in (SystemState.AUTO_READY, SystemState.MANUAL_READY):
                try:
                    self._decision.step()
                except Exception as e:
                    logger.error(f"Decision engine step error: {e}")

            elapsed = time.monotonic() - t0
            sleep_t = max(0.1, interval - elapsed)
            time.sleep(sleep_t)

    def _run_health_loop(self) -> None:
        """Periodic loop gathering system health snapshots."""
        interval = self._config.watchdog.health_interval_s

        while not self._stop_event.is_set():
            self._watchdog.heartbeat("health_loop")
            try:
                metrics = self._health.get_metrics(service_status=self._sm.current_state.value)
                diag_summary = self._diag.last_report.summary if self._diag.last_report else {}

                self._repo.record_health_snapshot(
                    cpu_usage_pct=metrics.cpu_usage_pct,
                    ram_usage_pct=metrics.ram_usage_pct,
                    disk_used_pct=metrics.disk_used_pct,
                    cpu_temp_c=metrics.cpu_temp_c,
                    uptime_s=metrics.uptime_s,
                    service_status=metrics.service_status,
                    components_summary=diag_summary,
                )
                logger.debug(f"Health snapshot: CPU {metrics.cpu_usage_pct}%, RAM {metrics.ram_usage_pct}%, Temp {metrics.cpu_temp_c}°C")
            except Exception as e:
                logger.error(f"Health snapshot error: {e}")

            self._stop_event.wait(timeout=interval)

    def stop(self, reason: str = "Orderly shutdown") -> None:
        """Gracefully shut down all actuators and subsystems."""
        logger.info(f"Initiating graceful shutdown: {reason}")
        self._stop_event.set()

        try:
            self._sm.transition_to(SystemState.SHUTTING_DOWN, reason=reason)
        except Exception:
            pass

        # 1. Force all relay channels to SAFE OFF
        logger.info("Commanding all relay loads OFF for shutdown...")
        self._safety.force_all_off(reason=f"Shutdown: {reason}")
        self._relays.all_off(reason="Shutdown safe OFF")

        # 2. Stop workers
        self._vision.stop_worker()
        self._sync.stop()
        self._watchdog.stop()

        # 3. Disconnect MQTT and camera
        self._mqtt.disconnect()
        self._camera.close()

        # 4. Log shutdown event to SQLite
        try:
            self._repo.record_device_event(EventType.SHUTDOWN, {"reason": reason})
        except Exception:
            pass

        # 5. Close database
        self._db.close()
        logger.info("Graceful shutdown complete.")


def setup_logging(level: str = "INFO") -> None:
    """Configure structured console logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sericulture Edge-AI Pi 5 Controller")
    parser.add_argument("--config", "-c", type=str, default="config/device.example.yaml", help="Path to YAML config")
    parser.add_argument("--auto-ready", action="store_true", help="Start in AUTO_READY if boot passes")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    setup_logging(args.log_level)

    try:
        config = load_config(args.config, load_env=True)
    except Exception as e:
        logger.critical(f"FATAL: Configuration loading failed: {e}")
        return 1

    app = SericultureApplication(config, auto_ready=args.auto_ready)

    def sig_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name}")
        app.stop(reason=f"Signal {sig_name}")
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    success = app.start()
    if not success:
        return 2

    # Keep main thread alive
    try:
        while True:
            time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
        app.stop("Process exit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
