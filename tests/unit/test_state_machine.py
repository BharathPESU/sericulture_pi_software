"""Unit tests for the safe-boot state machine and legal transitions."""

import pytest
from sericulture.event_types import SystemState
from sericulture.state_machine import IllegalTransitionError, StateMachine


def test_initial_state_is_booting():
    sm = StateMachine()
    assert sm.current_state == SystemState.BOOTING
    assert sm.boot_id is not None


def test_legal_boot_transition_sequence():
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, reason="Relays established safe OFF")
    sm.transition_to(SystemState.DIAGNOSTICS, reason="Running component checks")
    sm.transition_to(SystemState.SELF_TEST, reason="Self-tests passed")
    sm.transition_to(SystemState.AUTO_READY, reason="Ready for automatic control")
    assert sm.current_state == SystemState.AUTO_READY


def test_illegal_transition_raises():
    sm = StateMachine()
    # BOOTING directly to AUTO_READY is strictly forbidden
    with pytest.raises(IllegalTransitionError):
        sm.transition_to(SystemState.AUTO_READY, reason="Attempt illegal jump")


def test_fault_lockout_and_diagnostics_retry():
    sm = StateMachine()
    sm.transition_to(SystemState.SAFE_OUTPUTS, reason="Safe OFF")
    sm.transition_to(SystemState.DIAGNOSTICS, reason="Checking components")
    sm.transition_to(SystemState.FAULT_LOCKOUT, reason="SHT40 missing")
    assert sm.current_state == SystemState.FAULT_LOCKOUT

    # Fault lockout may transition back to DIAGNOSTICS to retry
    sm.transition_to(SystemState.DIAGNOSTICS, reason="Retry diagnostics after backoff")
    assert sm.current_state == SystemState.DIAGNOSTICS
