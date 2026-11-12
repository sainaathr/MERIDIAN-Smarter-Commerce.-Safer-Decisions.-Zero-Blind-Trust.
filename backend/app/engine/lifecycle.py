from typing import Dict, Set
from app.models.enums import TransactionLifecycleState

class IllegalStateTransitionError(ValueError):
    """Raised when an unauthorized or invalid state transition is attempted."""
    pass

# Explicit deterministic state transition matrix
LEGAL_TRANSITIONS: Dict[TransactionLifecycleState, Set[TransactionLifecycleState]] = {
    TransactionLifecycleState.DRAFT: {
        TransactionLifecycleState.PROPOSED,
        TransactionLifecycleState.CANCELLED,
    },
    TransactionLifecycleState.PROPOSED: {
        TransactionLifecycleState.POLICY_CHECK,
        TransactionLifecycleState.REJECTED,
        TransactionLifecycleState.CANCELLED,
    },
    TransactionLifecycleState.POLICY_CHECK: {
        TransactionLifecycleState.AWAITING_APPROVAL,
        TransactionLifecycleState.APPROVED, # Allowable for Mode B automatic reorder
        TransactionLifecycleState.REJECTED,
        TransactionLifecycleState.CANCELLED,
    },
    TransactionLifecycleState.AWAITING_APPROVAL: {
        TransactionLifecycleState.APPROVED,
        TransactionLifecycleState.REJECTED,
        TransactionLifecycleState.EXPIRED,
        TransactionLifecycleState.CANCELLED,
    },
    TransactionLifecycleState.APPROVED: {
        TransactionLifecycleState.PAYMENT_PENDING,
        TransactionLifecycleState.REJECTED, # Post-approval tampering invalidation
        TransactionLifecycleState.EXPIRED,
        TransactionLifecycleState.CANCELLED,
    },
    TransactionLifecycleState.PAYMENT_PENDING: {
        TransactionLifecycleState.PAID,
        TransactionLifecycleState.FAILED,
        TransactionLifecycleState.CANCELLED,
    },
    # Terminal states have no outbound transitions
    TransactionLifecycleState.PAID: set(),
    TransactionLifecycleState.REJECTED: set(),
    TransactionLifecycleState.EXPIRED: set(),
    TransactionLifecycleState.FAILED: set(),
    TransactionLifecycleState.CANCELLED: set(),
}

TERMINAL_STATES: Set[TransactionLifecycleState] = {
    TransactionLifecycleState.PAID,
    TransactionLifecycleState.REJECTED,
    TransactionLifecycleState.EXPIRED,
    TransactionLifecycleState.FAILED,
    TransactionLifecycleState.CANCELLED,
}

# Payment-restricted states: States that can ONLY be transitioned to by verified
# payment execution subsystems (e.g. PayPal capture confirmation or verified reconciliation).
# Generic lifecycle endpoints and non-payment services MUST NEVER transition directly to these states.
PAYMENT_RESTRICTED_STATES: Set[TransactionLifecycleState] = {
    TransactionLifecycleState.PAID,
}

def validate_transition(
    current_state: TransactionLifecycleState,
    target_state: TransactionLifecycleState,
) -> bool:
    """
    Checks if moving from current_state to target_state is permitted.
    Returns True if valid, raises IllegalStateTransitionError otherwise.
    """
    if current_state == target_state:
        # Idempotent re-affirmation is allowed
        return True

    allowed = LEGAL_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        raise IllegalStateTransitionError(
            f"Illegal transaction transition: cannot transition from '{current_state.value}' "
            f"to '{target_state.value}'. Allowed transitions: {[s.value for s in allowed]}."
        )
    return True

def is_terminal(state: TransactionLifecycleState) -> bool:
    """Checks whether the state is a finalized terminal state."""
    return state in TERMINAL_STATES
