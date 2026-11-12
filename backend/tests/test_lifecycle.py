import pytest
from app.models.enums import TransactionLifecycleState
from app.engine.lifecycle import validate_transition, IllegalStateTransitionError, is_terminal, TERMINAL_STATES

def test_legal_standard_transitions():
    """Verify standard happy path transitions."""
    assert validate_transition(TransactionLifecycleState.DRAFT, TransactionLifecycleState.PROPOSED) is True
    assert validate_transition(TransactionLifecycleState.PROPOSED, TransactionLifecycleState.POLICY_CHECK) is True
    assert validate_transition(TransactionLifecycleState.POLICY_CHECK, TransactionLifecycleState.AWAITING_APPROVAL) is True
    assert validate_transition(TransactionLifecycleState.AWAITING_APPROVAL, TransactionLifecycleState.APPROVED) is True
    assert validate_transition(TransactionLifecycleState.APPROVED, TransactionLifecycleState.PAYMENT_PENDING) is True
    assert validate_transition(TransactionLifecycleState.PAYMENT_PENDING, TransactionLifecycleState.PAID) is True

def test_legal_mode_b_auto_approval():
    """Mode B permits direct transition from POLICY_CHECK to APPROVED."""
    assert validate_transition(TransactionLifecycleState.POLICY_CHECK, TransactionLifecycleState.APPROVED) is True

def test_legal_tamper_invalidation_transition():
    """Approved proposals can be rejected if tampered post-quote."""
    assert validate_transition(TransactionLifecycleState.APPROVED, TransactionLifecycleState.REJECTED) is True

def test_idempotent_self_transitions():
    """Self-transitions are allowed idempotently."""
    assert validate_transition(TransactionLifecycleState.APPROVED, TransactionLifecycleState.APPROVED) is True
    assert validate_transition(TransactionLifecycleState.PAID, TransactionLifecycleState.PAID) is True

def test_illegal_skipping_transitions():
    """Skipping steps must be blocked."""
    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.DRAFT, TransactionLifecycleState.PAID)

    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.PROPOSED, TransactionLifecycleState.APPROVED)

    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.POLICY_CHECK, TransactionLifecycleState.PAID)

def test_terminal_states_cannot_transition():
    """Terminal states (PAID, REJECTED, EXPIRED, FAILED, CANCELLED) cannot transition outbound."""
    for term_state in TERMINAL_STATES:
        assert is_terminal(term_state) is True
        for target in [TransactionLifecycleState.DRAFT, TransactionLifecycleState.PROPOSED, TransactionLifecycleState.APPROVED]:
            with pytest.raises(IllegalStateTransitionError):
                validate_transition(term_state, target)
