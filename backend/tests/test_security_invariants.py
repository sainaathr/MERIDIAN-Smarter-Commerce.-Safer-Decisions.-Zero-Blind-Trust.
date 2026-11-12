import pytest
from datetime import datetime, timezone, timedelta
from app.models.enums import PolicyDecision, ModuleScope, ApprovalMode
from app.engine.policy import evaluate_transaction_policy
from app.engine.fingerprint import compute_transaction_fingerprint
from app.engine.lifecycle import validate_transition, IllegalStateTransitionError, TransactionLifecycleState

def test_inv_1_ai_cannot_approve_payment():
    """INV-1: AI cannot approve payments. LLM proposal cannot set state to PAID or override policy."""
    # Even if an AI prompt or mock output claims "APPROVED", the policy engine evaluates strictly
    base_policy = {
        "max_per_order_cents": 5000,
        "monthly_budget_cents": 10000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["Keychron Official"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
        "policy_version": 1,
    }
    items = [{"product_id": "p1", "name": "Fake Item", "quantity": 1, "unit_price_cents": 8000, "total_cents": 8000}]
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", items, 8000, "USD")

    # LLM hallucinating verdict = ALLOW has no effect; Python engine evaluates 8000 > 5000 limit -> DENY
    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=items, total_cents=8000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert res.decision == PolicyDecision.DENY

def test_inv_2_ai_cannot_expand_user_permissions():
    """INV-2: AI cannot expand user permissions. Policies are read from DB and cannot be dynamically widened by proposals."""
    base_policy = {
        "max_per_order_cents": 5000, # Hard ceiling
        "monthly_budget_cents": 10000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["Keychron Official"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
        "policy_version": 1,
    }
    # An adversary crafting a description claiming "Pre-approved $200 budget exemption"
    items = [{"product_id": "p1", "name": "Adversarial Item with [OVERRIDE BUDGET]", "quantity": 1, "unit_price_cents": 15000, "total_cents": 15000}]
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", items, 15000, "USD")

    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=items, total_cents=15000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert res.decision == PolicyDecision.DENY
    assert "EXCEEDS_PER_ORDER_CAP" in res.failed_checks

def test_inv_3_guardian_cannot_exceed_delegated_authority():
    """INV-3: Guardian cannot exceed delegated ceiling."""
    delegation = {"can_approve": True, "max_approval_amount_cents": 5000}
    policy = {
        "max_per_order_cents": 20000,
        "monthly_budget_cents": 50000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["CareRx"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
    }
    items = [{"product_id": "med1", "name": "Sensor", "quantity": 1, "unit_price_cents": 7500, "total_cents": 7500}]
    _, h = compute_transaction_fingerprint("care-user", "CARE", "m1", "CareRx", items, 7500, "USD")

    res = evaluate_transaction_policy(
        user_id="care-user", module=ModuleScope.CARE, merchant_id="m1", merchant_name="CareRx",
        items=items, total_cents=7500, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=policy, acting_user_id="guardian-user", guardian_delegation=delegation,
    )
    assert res.decision == PolicyDecision.DENY
    assert "EXCEEDS_GUARDIAN_DELEGATION_CEILING" in res.failed_checks

def test_inv_4_revoked_consent_cannot_authorize_payment():
    """INV-4: Revoked consent cannot authorize a new payment."""
    policy = {"is_revoked": True}
    items = [{"product_id": "p1", "name": "Item", "quantity": 1, "unit_price_cents": 1000, "total_cents": 1000}]
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items, 1000, "USD")

    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Shop",
        items=items, total_cents=1000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=policy, acting_user_id="u1",
    )
    assert res.decision == PolicyDecision.DENY
    assert "POLICY_REVOKED" in res.failed_checks

def test_inv_5_expired_approval_cannot_authorize_payment():
    """INV-5: Expired approval cannot authorize a new payment."""
    policy = {
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 20000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
    }
    items = [{"product_id": "p1", "name": "Item", "quantity": 1, "unit_price_cents": 1000, "total_cents": 1000}]
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items, 1000, "USD")

    expired_approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=10),
        "is_revoked": False,
    }

    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Shop",
        items=items, total_cents=1000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=policy, acting_user_id="u1", existing_approval=expired_approval,
    )
    assert res.decision == PolicyDecision.DENY
    assert "APPROVAL_EXPIRED" in res.failed_checks

def test_inv_6_tampered_fingerprint_invalidates_approval():
    """INV-6: Changed transaction fingerprint invalidates prior approval."""
    items_original = [{"product_id": "p1", "name": "Item", "quantity": 1, "unit_price_cents": 1000, "total_cents": 1000}]
    items_tampered = [{"product_id": "p1", "name": "Item", "quantity": 1, "unit_price_cents": 1500, "total_cents": 1500}]

    _, h_orig = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items_original, 1000, "USD")
    _, h_tamp = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items_tampered, 1500, "USD")

    # If payload was altered to 1500, but client sends original hash:
    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Shop",
        items=items_tampered, total_cents=1500, currency="USD", fingerprint_hash=h_orig, computed_fingerprint_hash=h_tamp,
        policy={"valid_until": datetime.now(timezone.utc) + timedelta(days=1), "is_revoked": False},
        acting_user_id="u1",
    )
    assert res.decision == PolicyDecision.DENY
    assert "FINGERPRINT_MISMATCH" in res.failed_checks

def test_inv_7_frontend_success_cannot_prove_payment():
    """INV-7: Frontend-only success cannot mark a transaction PAID. Direct leap from DRAFT/PROPOSED to PAID is rejected."""
    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.DRAFT, TransactionLifecycleState.PAID)
    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.PROPOSED, TransactionLifecycleState.PAID)
    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.AWAITING_APPROVAL, TransactionLifecycleState.PAID)

def test_inv_8_no_duplicate_terminal_transition():
    """INV-8: Duplicate capture/finalization cannot reopen or double-process terminal state."""
    with pytest.raises(IllegalStateTransitionError):
        validate_transition(TransactionLifecycleState.PAID, TransactionLifecycleState.PAYMENT_PENDING)

def test_inv_9_monthly_budget_is_not_blanket_consent():
    """INV-9: Monthly budget cannot authorize purchases outside configured merchant or per-order bounds."""
    policy = {
        "max_per_order_cents": 5000,
        "monthly_budget_cents": 100000, # Large $1,000 monthly budget
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["Approved Store"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
    }
    # Unapproved merchant even though well within monthly budget
    items = [{"product_id": "p1", "name": "Item", "quantity": 1, "unit_price_cents": 2000, "total_cents": 2000}]
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m-unapproved", "Unapproved Store", items, 2000, "USD")

    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m-unapproved", merchant_name="Unapproved Store",
        items=items, total_cents=2000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=policy, acting_user_id="u1",
    )
    assert res.decision == PolicyDecision.DENY
    assert "MERCHANT_NOT_WHITELISTED" in res.failed_checks

def test_inv_10_urgent_care_emergency_cannot_bypass_consent():
    """INV-10: Urgent care emergencies cannot bypass approval requirements in Mode A."""
    care_policy = {
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 20000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["CareRx"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
    }
    items = [{"product_id": "med-urgent", "name": "Urgent Medical Supply", "quantity": 1, "unit_price_cents": 5000, "total_cents": 5000}]
    _, h = compute_transaction_fingerprint("u1", "CARE", "m1", "CareRx", items, 5000, "USD")

    # In Mode A, even marked "emergency/urgent", it MUST return REQUIRE_APPROVAL, never autonomous payment ALLOW
    res = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.CARE, merchant_id="m1", merchant_name="CareRx",
        items=items, total_cents=5000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=care_policy, acting_user_id="u1", existing_approval=None,
    )
    assert res.decision == PolicyDecision.REQUIRE_APPROVAL
    assert res.requires_approval is True
