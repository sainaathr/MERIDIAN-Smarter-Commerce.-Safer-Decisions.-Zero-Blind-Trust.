import pytest
from datetime import datetime, timezone, timedelta
from app.models.enums import PolicyDecision, ModuleScope, ApprovalMode
from app.engine.policy import evaluate_transaction_policy
from app.engine.fingerprint import compute_transaction_fingerprint

@pytest.fixture
def base_policy():
    return {
        "max_per_order_cents": 15000, # $150.00
        "monthly_budget_cents": 40000, # $400.00
        "monthly_spent_cents": 5000,   # $50.00
        "currency": "USD",
        "allowed_merchants": ["Keychron Official", "Anker Direct"],
        "allowed_categories": ["Peripherals", "Cables"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
        "policy_version": 1,
    }

@pytest.fixture
def base_items():
    return [{
        "product_id": "p-1",
        "name": "Keychron Keyboard",
        "sku": "k-01",
        "category": "Peripherals",
        "quantity": 1,
        "unit_price_cents": 8400,
        "total_cents": 8400,
    }]

def test_tamper_detection_denial(base_policy, base_items):
    """If supplied fingerprint does not match computed hash, must return DENY immediately."""
    result = evaluate_transaction_policy(
        user_id="u1",
        module=ModuleScope.PERSONAL,
        merchant_id="m1",
        merchant_name="Keychron Official",
        items=base_items,
        total_cents=8400,
        currency="USD",
        fingerprint_hash="forged_or_old_hash_0000000000000000000000000000000000000000000000",
        computed_fingerprint_hash="legit_computed_hash_11111111111111111111111111111111111111111111",
        policy=base_policy,
        acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "FINGERPRINT_MISMATCH" in result.failed_checks
    assert "SECURITY DENIAL" in result.human_explanation

def test_missing_policy_requires_approval(base_items):
    """When no policy is configured, transaction requires explicit human approval."""
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1",
        module=ModuleScope.PERSONAL,
        merchant_id="m1",
        merchant_name="Keychron Official",
        items=base_items,
        total_cents=8400,
        currency="USD",
        fingerprint_hash=h,
        computed_fingerprint_hash=h,
        policy=None,
        acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.requires_approval is True
    assert "NO_POLICY_CONFIGURED" in result.failed_checks

def test_revoked_policy_denies(base_policy, base_items):
    """Revoked policy must immediately DENY."""
    base_policy["is_revoked"] = True
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "POLICY_REVOKED" in result.failed_checks

def test_expired_policy_denies(base_policy, base_items):
    """Expired policy must immediately DENY."""
    base_policy["valid_until"] = datetime.now(timezone.utc) - timedelta(days=1)
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "POLICY_EXPIRED" in result.failed_checks

def test_currency_mismatch_denies(base_policy, base_items):
    """Currency mismatch between transaction and policy must DENY."""
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", base_items, 8400, "EUR")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="EUR", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "CURRENCY_MISMATCH" in result.failed_checks

def test_non_whitelisted_merchant_denies(base_policy, base_items):
    """Unapproved merchant must DENY."""
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m-shady", "Shady Drop Shipper", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m-shady", merchant_name="Shady Drop Shipper",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "MERCHANT_NOT_WHITELISTED" in result.failed_checks

def test_exceeds_per_order_cap_denies(base_policy, base_items):
    """Total exceeding max_per_order_cents ($150.00 = 15000 cents) must DENY."""
    expensive_items = [{"product_id": "p-1", "name": "Keychron Pro", "category": "Peripherals", "quantity": 1, "unit_price_cents": 18000, "total_cents": 18000}]
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", expensive_items, 18000, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=expensive_items, total_cents=18000, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "EXCEEDS_PER_ORDER_CAP" in result.failed_checks

def test_exceeds_monthly_budget_denies(base_policy, base_items):
    """Transaction pushing monthly spend over budget must DENY."""
    base_policy["monthly_spent_cents"] = 35000 # $350 spent out of $400 budget
    # Order is $84.00 (8400 cents) -> 35000 + 8400 = 43400 > 40000
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.DENY
    assert "EXCEEDS_MONTHLY_BUDGET" in result.failed_checks

def test_guardian_scope_enforcement(base_policy, base_items):
    """Guardian cannot approve beyond their delegated ceiling."""
    guardian_delegation = {
        "can_approve": True,
        "max_approval_amount_cents": 5000, # Guardian capped at $50.00
    }
    # Proposal total is $84.00 (8400 cents)
    _, h = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Keychron Official", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.PERSONAL, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=base_policy, acting_user_id="guardian-99", guardian_delegation=guardian_delegation,
    )
    assert result.decision == PolicyDecision.DENY
    assert "EXCEEDS_GUARDIAN_DELEGATION_CEILING" in result.failed_checks

def test_care_mode_a_requires_approval(base_items):
    """CARE Mode A (Explicit always) requires approval before ALLOW."""
    care_policy = {
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 30000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["Keychron Official"],
        "allowed_categories": ["Peripherals"],
        "approval_mode": ApprovalMode.EXPLICIT_ALWAYS,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
        "policy_version": 1,
    }
    _, h = compute_transaction_fingerprint("u1", "CARE", "m1", "Keychron Official", base_items, 8400, "USD")
    # Without existing approval -> REQUIRE_APPROVAL
    res1 = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.CARE, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=care_policy, acting_user_id="u1", existing_approval=None,
    )
    assert res1.decision == PolicyDecision.REQUIRE_APPROVAL

    # With valid approval bound to hash -> ALLOW
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
        "is_revoked": False,
    }
    res2 = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.CARE, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=care_policy, acting_user_id="u1", existing_approval=approval,
    )
    assert res2.decision == PolicyDecision.ALLOW

def test_care_mode_b_controlled_auto_allows_when_all_conditions_met(base_items):
    """CARE Mode B (Controlled Auto) directly returns ALLOW when within all pre-authorized bounds."""
    care_auto_policy = {
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 30000,
        "monthly_spent_cents": 0,
        "currency": "USD",
        "allowed_merchants": ["Keychron Official"],
        "allowed_categories": ["Peripherals"],
        "approval_mode": ApprovalMode.CONTROLLED_AUTO,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "is_revoked": False,
        "policy_version": 1,
    }
    _, h = compute_transaction_fingerprint("u1", "CARE", "m1", "Keychron Official", base_items, 8400, "USD")
    result = evaluate_transaction_policy(
        user_id="u1", module=ModuleScope.CARE, merchant_id="m1", merchant_name="Keychron Official",
        items=base_items, total_cents=8400, currency="USD", fingerprint_hash=h, computed_fingerprint_hash=h,
        policy=care_auto_policy, acting_user_id="u1",
    )
    assert result.decision == PolicyDecision.ALLOW
    assert "CONTROLLED_AUTO_CONDITIONS_SATISFIED" in result.passed_checks
