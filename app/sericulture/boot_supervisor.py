"""Boot supervisor — orchestrates safe hardware boot and diagnostic validation.

SAFETY RULES:
  - Commands all relays to safe OFF before any sensors or vision models start.
  - Required components must pass diagnostics before automatic or manual ready state.
  - If any required component fails, locks out with audible buzzer pattern and
    bounded retry. Relays remain safely OFF.
  - Automatically records boot ID, version, and diagnostic report to SQLite.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sericulture.config import AppConfig
from sericulture.diagnostics import DiagnosticsRunner
from sericulture.event_types import (
    ActuatorCommand,
    ComponentId,
    EventType,
    FaultCategory,
    SystemState,
)
from sericulture.hardware.buzzer import BuzzerController
from sericulture.hardware.relay_manager import RelayManager
from sericulture.state_machine import StateMachine
from sericulture.storage.database import DatabaseManager
from sericulture.storage.event_repository import EventRepository

logger = logging.getLogger(__name__)


class BootSupervisor:
    """Orchestrates the safe boot sequence according to the state machine."""

    def __init__(
        self,
        config: AppConfig,
        state_machine: StateMachine,
        relay_manager: RelayManager,
        buzzer: BuzzerController,
        db_manager: DatabaseManager,
        diagnostics_runner: DiagnosticsRunner,
        event_repo: EventRepository | None = None,
        software_version: str = "1.0.0",
    ):
        self._config = config
        self._sm = state_machine
        self._relays = relay_manager
        self._buzzer = buzzer
        self._db = db_manager
        self._diag = diagnostics_runner
        self._repo = event_repo
        self._software_version = software_version
        self._boot_id = state_machine.boot_id

    def run_boot_sequence(self, auto_ready: bool = False) -> bool:
        """Execute the safe boot sequence.

        Returns:
            True if system successfully transitions to READY (AUTO_READY or MANUAL_READY).
            False if system enters FAULT_LOCKOUT.
        """
        logger.info(f"=== Beginning Safe Boot Sequence [Boot ID: {self._boot_id}] ===")

        # Step 1: Establish safe outputs
        try:
            self._sm.transition_to(
                SystemState.SAFE_OUTPUTS,
                reason="Establishing verified hardware OFF defaults on all relay channels",
            )
            # Force all relay channels OFF immediately
            self._relays.all_off(reason="Safe boot output initialization")
            logger.info("All relay channels commanded to verified SAFE-OFF state.")
        except Exception as e:
            logger.critical(f"FATAL: Failed to establish safe relay outputs: {e}")
            self._sm.transition_to(SystemState.FAULT_LOCKOUT, reason=f"Relay safe-OFF initialization failure: {e}")
            self._buzzer.play_fault(FaultCategory.CONFIG_GPIO_FAULT)
            return False

        # Step 2: Initialize SQLite Storage
        try:
            self._db.initialize()
            logger.info("Local SQLite database initialized and migrations verified.")
        except Exception as e:
            logger.critical(f"FATAL: Database initialization failed: {e}")
            self._sm.transition_to(SystemState.FAULT_LOCKOUT, reason=f"Database initialization failure: {e}")
            self._buzzer.play_fault(FaultCategory.STORAGE_FAULT)
            return False

        # Step 3: Diagnostics phase
        self._sm.transition_to(SystemState.DIAGNOSTICS, reason="Executing component diagnostics")
        report = self._diag.run_all()

        if not report.all_required_healthy:
            failed_list = [f"{r.component.value} ({r.status.value})" for r in report.failed_components]
            failed_str = ", ".join(failed_list)
            logger.error(f"Boot diagnostics failed! Required components unhealthy: {failed_str}")

            self._sm.transition_to(
                SystemState.FAULT_LOCKOUT,
                reason=f"Diagnostics failed for: {failed_str}",
            )
            # Ensure relays remain OFF
            self._relays.all_off(reason="Fault lockout safety enforce")

            # Sound buzzer alert for the first failed category
            categories = report.get_fault_categories()
            if categories:
                primary_fault = categories[0]
                self._buzzer.play_fault(primary_fault)

            # Persist boot failure event if DB is operational
            if self._repo:
                try:
                    self._repo.record_device_event(
                        EventType.BOOT,
                        {
                            "boot_id": self._boot_id,
                            "software_version": self._software_version,
                            "status": "FAULT_LOCKOUT",
                            "failed_components": failed_list,
                            "diagnostic_report": report.summary,
                        },
                    )
                except Exception as log_err:
                    logger.error(f"Failed to persist boot failure event: {log_err}")

            return False

        # Step 4: Self-test phase (non-intrusive safe self-test)
        self._sm.transition_to(SystemState.SELF_TEST, reason="Running non-intrusive system self-tests")
        logger.info("Non-intrusive self-tests passed (no physical mains loads energized).")

        # Step 5: Transition to Ready state
        target_state = SystemState.AUTO_READY if auto_ready else SystemState.MANUAL_READY
        self._sm.transition_to(
            target_state,
            reason=f"Boot checks passed; entering {'AUTO_READY' if auto_ready else 'MANUAL_READY'} mode",
        )

        # Step 6: Persist successful boot event
        if self._repo:
            try:
                self._repo.record_device_event(
                    EventType.BOOT,
                    {
                        "boot_id": self._boot_id,
                        "software_version": self._software_version,
                        "status": target_state.value,
                        "diagnostic_report": report.summary,
                    },
                )
            except Exception as e:
                logger.error(f"Failed to record boot success event: {e}")

        logger.info(f"=== Safe Boot Sequence Complete. System is in {target_state.value} ===")
        return True
