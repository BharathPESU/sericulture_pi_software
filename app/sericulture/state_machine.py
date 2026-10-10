"""Safe-boot state machine with explicit transitions and persistence.

SAFETY: State transitions are validated — only legal transitions are allowed.
All transitions are logged and persisted. Ad-hoc boolean checks are replaced
by querying the current state through this machine.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Callable

from sericulture.event_types import SystemState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StateTransition:
    """Record of a state machine transition."""
    from_state: SystemState
    to_state: SystemState
    reason: str
    timestamp_utc: datetime
    monotonic_ns: int
    boot_id: str


# ──────────────────────────────────────────────────────────────────────
# Legal transition table
# ──────────────────────────────────────────────────────────────────────

# Maps each state to the set of states it may transition to.
# Any transition not listed here is ILLEGAL and will be rejected.
LEGAL_TRANSITIONS: dict[SystemState, frozenset[SystemState]] = {
    SystemState.BOOTING: frozenset({
        SystemState.SAFE_OUTPUTS,
        SystemState.FAULT_LOCKOUT,       # Config/GPIO failure at very early boot
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.SAFE_OUTPUTS: frozenset({
        SystemState.DIAGNOSTICS,
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.DIAGNOSTICS: frozenset({
        SystemState.SELF_TEST,
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.SELF_TEST: frozenset({
        SystemState.MANUAL_READY,
        SystemState.AUTO_READY,
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.FAULT_LOCKOUT: frozenset({
        SystemState.DIAGNOSTICS,         # Retry diagnostics after interval
        SystemState.RECOVERY_PENDING,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.MANUAL_READY: frozenset({
        SystemState.AUTO_READY,          # Manual → Auto selector switch
        SystemState.MAINS_LOST,
        SystemState.OFFLINE_OPERATION,
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.AUTO_READY: frozenset({
        SystemState.MANUAL_READY,        # Auto → Manual selector switch
        SystemState.MAINS_LOST,
        SystemState.OFFLINE_OPERATION,
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.MAINS_LOST: frozenset({
        SystemState.RECOVERY_PENDING,    # Mains restored → recovery checks
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.OFFLINE_OPERATION: frozenset({
        SystemState.MANUAL_READY,        # Internet restored
        SystemState.AUTO_READY,          # Internet restored
        SystemState.MAINS_LOST,
        SystemState.FAULT_LOCKOUT,
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.RECOVERY_PENDING: frozenset({
        SystemState.MANUAL_READY,        # Recovery checks passed
        SystemState.AUTO_READY,          # Recovery checks passed
        SystemState.FAULT_LOCKOUT,       # Recovery checks failed
        SystemState.SHUTTING_DOWN,
    }),
    SystemState.SHUTTING_DOWN: frozenset(),  # Terminal state — no transitions out
}


class StateMachine:
    """Thread-safe state machine with validated transitions.

    Usage:
        sm = StateMachine(boot_id="abc123")
        sm.transition_to(SystemState.SAFE_OUTPUTS, "GPIO initialized")
        if sm.allows_auto_control:
            ...
    """

    def __init__(self, boot_id: str | None = None):
        self._state = SystemState.BOOTING
        self._boot_id = boot_id or str(uuid.uuid4())
        self._lock = Lock()
        self._history: list[StateTransition] = []
        self._listeners: list[Callable[[StateTransition], None]] = []
        self._entered_at_mono = time.monotonic_ns()
        logger.info("State machine initialized in %s (boot_id=%s)", self._state.value, self._boot_id)

    @property
    def current_state(self) -> SystemState:
        """Current system state (thread-safe read)."""
        with self._lock:
            return self._state

    @property
    def boot_id(self) -> str:
        return self._boot_id

    @property
    def history(self) -> list[StateTransition]:
        """Copy of transition history."""
        with self._lock:
            return list(self._history)

    @property
    def allows_auto_control(self) -> bool:
        """Whether the current state permits automatic actuator control."""
        return self.current_state == SystemState.AUTO_READY

    @property
    def allows_manual_control(self) -> bool:
        """Whether the current state permits manual actuator control."""
        return self.current_state in (
            SystemState.MANUAL_READY,
            SystemState.AUTO_READY,
        )

    @property
    def allows_ai_workload(self) -> bool:
        """Whether the current state permits AI/vision workloads."""
        return self.current_state in (
            SystemState.MANUAL_READY,
            SystemState.AUTO_READY,
            SystemState.OFFLINE_OPERATION,
        )

    @property
    def is_safe_for_sensing(self) -> bool:
        """Whether the current state permits sensor reads (nearly always)."""
        return self.current_state not in (
            SystemState.SHUTTING_DOWN,
        )

    @property
    def is_in_fault(self) -> bool:
        return self.current_state == SystemState.FAULT_LOCKOUT

    @property
    def is_shutting_down(self) -> bool:
        return self.current_state == SystemState.SHUTTING_DOWN

    def add_listener(self, callback: Callable[[StateTransition], None]) -> None:
        """Register a callback for state transitions.

        Callbacks are invoked under the state lock — keep them fast and non-blocking.
        """
        with self._lock:
            self._listeners.append(callback)

    def transition_to(self, new_state: SystemState, reason: str) -> StateTransition:
        """Attempt a state transition.

        Args:
            new_state: Target state.
            reason: Human-readable reason for the transition.

        Returns:
            The recorded StateTransition.

        Raises:
            IllegalTransitionError: If the transition is not in the legal table.
        """
        with self._lock:
            old_state = self._state

            if new_state == old_state:
                logger.debug("State machine: no-op transition %s → %s", old_state.value, new_state.value)
                # Return a record but don't change state
                return StateTransition(
                    from_state=old_state,
                    to_state=new_state,
                    reason=f"(no-op) {reason}",
                    timestamp_utc=datetime.now(timezone.utc),
                    monotonic_ns=time.monotonic_ns(),
                    boot_id=self._boot_id,
                )

            allowed = LEGAL_TRANSITIONS.get(old_state, frozenset())
            if new_state not in allowed:
                msg = (
                    f"ILLEGAL state transition: {old_state.value} → {new_state.value}. "
                    f"Reason: {reason}. "
                    f"Allowed from {old_state.value}: {sorted(s.value for s in allowed)}"
                )
                logger.error(msg)
                raise IllegalTransitionError(old_state, new_state, reason)

            now_utc = datetime.now(timezone.utc)
            mono = time.monotonic_ns()

            transition = StateTransition(
                from_state=old_state,
                to_state=new_state,
                reason=reason,
                timestamp_utc=now_utc,
                monotonic_ns=mono,
                boot_id=self._boot_id,
            )

            self._state = new_state
            self._entered_at_mono = mono
            self._history.append(transition)

            duration_in_prev = (mono - self._entered_at_mono) / 1e9
            logger.info(
                "STATE: %s → %s | reason: %s | spent %.2fs in %s",
                old_state.value, new_state.value, reason,
                duration_in_prev, old_state.value,
            )

            # Notify listeners (under lock — keep callbacks fast)
            for listener in self._listeners:
                try:
                    listener(transition)
                except Exception:
                    logger.exception("State transition listener error")

            return transition

    def time_in_current_state_s(self) -> float:
        """Seconds spent in the current state (monotonic)."""
        with self._lock:
            return (time.monotonic_ns() - self._entered_at_mono) / 1e9

    def format_status(self) -> str:
        """Return a human-readable status string."""
        state = self.current_state
        in_state = self.time_in_current_state_s()
        n_transitions = len(self._history)
        return (
            f"State: {state.value} | "
            f"In state: {in_state:.1f}s | "
            f"Transitions: {n_transitions} | "
            f"Boot: {self._boot_id}"
        )


class IllegalTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""
    def __init__(self, from_state: SystemState, to_state: SystemState, reason: str):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal transition {from_state.value} → {to_state.value}: {reason}"
        )
