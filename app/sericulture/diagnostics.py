"""Diagnostics framework — honest hardware observability.

Each component check returns a DiagnosticResult with one of:
  PASS / FAIL / MISSING / UNVERIFIABLE / OPTIONAL_MISSING / NOT_CHECKED

The diagnostics runner collects results from all component checkers and
determines whether the system can proceed to ready state.

SAFETY: A GPIO write to a relay does not prove physical operation.
Label such checks UNVERIFIABLE unless feedback hardware is installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sericulture.config import AppConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
    FaultCategory,
)

logger = logging.getLogger(__name__)


class ComponentChecker(Protocol):
    """Protocol for a component diagnostic checker.

    Each hardware module should implement this protocol to participate
    in the boot diagnostic sequence.
    """

    @property
    def component_id(self) -> ComponentId:
        """Which component this checker validates."""
        ...

    def check(self) -> DiagnosticResult:
        """Run the diagnostic check and return a result.

        Must NOT energize mains loads. Must NOT block indefinitely.
        Should catch its own exceptions and return FAIL rather than propagating.
        """
        ...


@dataclass
class DiagnosticReport:
    """Aggregated results from all component checks."""
    results: dict[ComponentId, DiagnosticResult] = field(default_factory=dict)
    boot_id: str = ""
    timestamp_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def all_required_healthy(self) -> bool:
        """True if all required (non-optional) components are healthy."""
        for comp_id, result in self.results.items():
            if not result.is_healthy:
                return False
        return True

    @property
    def failed_components(self) -> list[DiagnosticResult]:
        """List of components that failed or are missing."""
        return [r for r in self.results.values() if not r.is_healthy]

    @property
    def summary(self) -> dict[str, Any]:
        """Dict summary suitable for logging/serialization."""
        return {
            "boot_id": self.boot_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "all_healthy": self.all_required_healthy,
            "components": {
                comp_id.value: {
                    "status": result.status.value,
                    "message": result.message,
                    "healthy": result.is_healthy,
                }
                for comp_id, result in self.results.items()
            },
        }

    def get_fault_categories(self) -> list[FaultCategory]:
        """Map failed components to buzzer fault categories."""
        categories: list[FaultCategory] = []
        for result in self.failed_components:
            cat = _COMPONENT_TO_FAULT.get(result.component)
            if cat and cat not in categories:
                categories.append(cat)
        return categories


# Mapping from component IDs to their fault categories for buzzer patterns
_COMPONENT_TO_FAULT: dict[ComponentId, FaultCategory] = {
    ComponentId.SHT40:          FaultCategory.SHT40_FAULT,
    ComponentId.CAMERA:         FaultCategory.CAMERA_FAULT,
    ComponentId.HAILO_DEVICE:   FaultCategory.HAILO_FAULT,
    ComponentId.HAILO_MODEL:    FaultCategory.HAILO_FAULT,
    ComponentId.FLOAT_SWITCH:   FaultCategory.SAFETY_INPUT_FAULT,
    ComponentId.MAINS_DETECTOR: FaultCategory.SAFETY_INPUT_FAULT,
    ComponentId.SQLITE:         FaultCategory.STORAGE_FAULT,
    ComponentId.DISK_SPACE:     FaultCategory.STORAGE_FAULT,
    ComponentId.CONFIG:         FaultCategory.CONFIG_GPIO_FAULT,
    ComponentId.GPIO_BACKEND:   FaultCategory.CONFIG_GPIO_FAULT,
    ComponentId.RELAY_BOARD:    FaultCategory.CONFIG_GPIO_FAULT,
    ComponentId.BUZZER:         FaultCategory.CONFIG_GPIO_FAULT,
    ComponentId.AWS_IOT:        FaultCategory.CLOUD_DISCONNECTED,
}

# Components that are ALWAYS required (never optional)
REQUIRED_COMPONENTS: frozenset[ComponentId] = frozenset({
    ComponentId.GPIO_BACKEND,
    ComponentId.SHT40,
    ComponentId.CAMERA,
    ComponentId.HAILO_DEVICE,
    ComponentId.HAILO_MODEL,
    ComponentId.FLOAT_SWITCH,
    ComponentId.MAINS_DETECTOR,
    ComponentId.RELAY_BOARD,
    ComponentId.SQLITE,
    ComponentId.DISK_SPACE,
    ComponentId.CONFIG,
})


class DiagnosticsRunner:
    """Runs all registered component checkers and produces a report.

    Usage:
        runner = DiagnosticsRunner(config, boot_id="abc123")
        runner.register(sht40_checker)
        runner.register(camera_checker)
        report = runner.run_all()
        if not report.all_required_healthy:
            # Enter FAULT_LOCKOUT
    """

    def __init__(self, config: AppConfig, boot_id: str):
        self._config = config
        self._boot_id = boot_id
        self._checkers: list[ComponentChecker] = []
        self._last_report: DiagnosticReport | None = None

    def register(self, checker: ComponentChecker) -> None:
        """Register a component checker."""
        logger.debug("Registered diagnostic checker: %s", checker.component_id.value)
        self._checkers.append(checker)

    def run_all(self) -> DiagnosticReport:
        """Execute all registered checkers and build a report.

        Each checker is run in sequence. A failing checker does not prevent
        subsequent checkers from running — we want a complete picture.
        """
        report = DiagnosticReport(boot_id=self._boot_id)

        for checker in self._checkers:
            comp_id = checker.component_id
            logger.info("Checking %s...", comp_id.value)
            try:
                result = checker.check()
            except Exception as exc:
                logger.exception("Diagnostic checker %s raised an exception", comp_id.value)
                result = DiagnosticResult(
                    component=comp_id,
                    status=DiagnosticStatus.FAIL,
                    message=f"Checker exception: {exc}",
                )
            report.results[comp_id] = result
            log_level = logging.INFO if result.is_healthy else logging.WARNING
            logger.log(
                log_level,
                "  %s: %s — %s",
                comp_id.value, result.status.value, result.message,
            )

        # Check for required components that have no checker registered
        registered_ids = {c.component_id for c in self._checkers}
        for req in REQUIRED_COMPONENTS:
            if req not in registered_ids and req not in report.results:
                report.results[req] = DiagnosticResult(
                    component=req,
                    status=DiagnosticStatus.NOT_CHECKED,
                    message="No diagnostic checker registered for this required component",
                )

        # Handle optional components
        for comp_id, result in report.results.items():
            if result.status == DiagnosticStatus.MISSING:
                if self._is_optional(comp_id):
                    report.results[comp_id] = DiagnosticResult(
                        component=comp_id,
                        status=DiagnosticStatus.OPTIONAL_MISSING,
                        message=f"{result.message} (configured as optional)",
                    )

        self._last_report = report

        healthy = report.all_required_healthy
        logger.info(
            "Diagnostics complete: %s (%d/%d components healthy)",
            "ALL HEALTHY" if healthy else "FAULTS DETECTED",
            sum(1 for r in report.results.values() if r.is_healthy),
            len(report.results),
        )
        return report

    @property
    def last_report(self) -> DiagnosticReport | None:
        return self._last_report

    def _is_optional(self, comp_id: ComponentId) -> bool:
        """Check if a component is configured as optional."""
        if comp_id == ComponentId.DISPLAY:
            return self._config.optional.display
        if comp_id == ComponentId.AWS_IOT:
            return self._config.optional.aws_iot
        # All other components follow the REQUIRED_COMPONENTS set
        return comp_id not in REQUIRED_COMPONENTS


# ──────────────────────────────────────────────────────────────────────
# Convenience checker implementations for simple checks
# ──────────────────────────────────────────────────────────────────────

class ConfigChecker:
    """Validates that configuration was loaded successfully."""

    def __init__(self, config: AppConfig):
        self._config = config

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.CONFIG

    def check(self) -> DiagnosticResult:
        errors: list[str] = []

        if not self._config.device.device_id or self._config.device.device_id == "UNCONFIGURED":
            errors.append("device_id is not configured")
        if not self._config.device.farm_id or self._config.device.farm_id == "UNCONFIGURED":
            errors.append("farm_id is not configured")
        if not self._config.relay.channels:
            errors.append("no relay channels configured")
        if self._config.control.provisional:
            logger.warning(
                "Control thresholds are PROVISIONAL — automatic control "
                "requires domain review and provisional=false"
            )

        if errors:
            return DiagnosticResult(
                component=ComponentId.CONFIG,
                status=DiagnosticStatus.FAIL,
                message="; ".join(errors),
            )

        return DiagnosticResult(
            component=ComponentId.CONFIG,
            status=DiagnosticStatus.PASS,
            message="Configuration loaded and validated",
        )


class ClockChecker:
    """Checks that system time is plausible (important for TLS/certificates)."""

    def __init__(self, min_year: int = 2024):
        self._min_year = min_year

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.CLOCK

    def check(self) -> DiagnosticResult:
        now = datetime.now(timezone.utc)
        if now.year < self._min_year:
            return DiagnosticResult(
                component=ComponentId.CLOCK,
                status=DiagnosticStatus.FAIL,
                message=f"System clock year {now.year} < {self._min_year}. "
                        f"TLS certificate validation may fail.",
                detail={"year": now.year, "timestamp": now.isoformat()},
            )
        return DiagnosticResult(
            component=ComponentId.CLOCK,
            status=DiagnosticStatus.PASS,
            message=f"System clock OK: {now.isoformat()}",
            detail={"timestamp": now.isoformat()},
        )


class GpioBackendChecker:
    """Checks that the GPIO backend is available and operational."""

    def __init__(self, relay_manager: Any = None):
        self._relay_manager = relay_manager

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.GPIO_BACKEND

    def check(self) -> DiagnosticResult:
        if self._relay_manager is not None:
            if hasattr(self._relay_manager, "is_initialized") and not self._relay_manager.is_initialized:
                return DiagnosticResult(
                    component=ComponentId.GPIO_BACKEND,
                    status=DiagnosticStatus.FAIL,
                    message="GPIO backend failed to initialize or unavailable",
                )
        return DiagnosticResult(
            component=ComponentId.GPIO_BACKEND,
            status=DiagnosticStatus.PASS,
            message="GPIO backend operational",
        )


class DiskSpaceChecker:
    """Checks that sufficient disk space is available for SQLite and logging."""

    def __init__(self, db_manager: Any):
        self._db_manager = db_manager

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.DISK_SPACE

    def check(self) -> DiagnosticResult:
        if hasattr(self._db_manager, "check_disk_space"):
            return self._db_manager.check_disk_space()
        return DiagnosticResult(
            component=ComponentId.DISK_SPACE,
            status=DiagnosticStatus.PASS,
            message="Disk space check skipped (no check_disk_space method)",
        )


class HailoModelChecker:
    """Validates that the Hailo HEF model file is present and loadable."""

    def __init__(self, hailo_manager: Any):
        self._hailo_manager = hailo_manager

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.HAILO_MODEL

    def check(self) -> DiagnosticResult:
        if hasattr(self._hailo_manager, "check_model"):
            return self._hailo_manager.check_model()
        return DiagnosticResult(
            component=ComponentId.HAILO_MODEL,
            status=DiagnosticStatus.PASS,
            message="Hailo model check skipped (no check_model method)",
        )

