import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models.enums import ModuleScope, PolicyDecision, ApprovalMode, TransactionLifecycleState, ApprovalType
from app.engine.policy import evaluate_transaction_policy
from app.engine.approval import verify_approval_integrity, verify_approver_authority, ensure_utc
from app.core.init_db import init_database

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_database()

# ==============================================================================
# 1. Approval Module Full Branch Coverage Tests
# ==============================================================================
def test_ensure_utc_branches():
    assert ensure_utc(None) is None
    assert ensure_utc("invalid-iso-date-string") is None

    iso_dt = ensure_utc("2026-11-08T12:00:00Z")
    assert iso_dt is not None
    assert iso_dt.tzinfo == timezone.utc

    naive = datetime(2026, 11, 8, 12, 0, 0)
    aware = ensure_utc(naive)
    assert aware.tzinfo == timezone.utc

def test_verify_approval_integrity_branches():
    # None approval
    res = verify_approval_integrity(None, "hash-1")
    assert not res.is_valid
    assert "No approval record found" in res.reason

    # Hash mismatch
    res = verify_approval_integrity({"fingerprint_hash": "hash-recorded"}, "hash-expected")
    assert not res.is_valid
    assert "Approval hash mismatch" in res.reason

    # Revoked approval
    res = verify_approval_integrity(
        {"fingerprint_hash": "hash-1", "is_revoked": True, "revoked_reason": "Suspected fraud"},
        "hash-1"
    )
    assert not res.is_valid
    assert "explicitly revoked" in res.reason

    # Expired approval
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    res = verify_approval_integrity(
        {"fingerprint_hash": "hash-1", "expires_at": past, "is_revoked": False},
        "hash-1"
    )
    assert not res.is_valid
    assert "Approval expired" in res.reason

    # Revoked underlying policy
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    res = verify_approval_integrity(
        {"fingerprint_hash": "hash-1", "expires_at": future, "is_revoked": False},
        "hash-1",
        policy={"is_revoked": True}
    )
    assert not res.is_valid
    assert "Underlying consent policy was revoked" in res.reason

    # Valid approval with unrevoked policy
    res = verify_approval_integrity(
        {"fingerprint_hash": "hash-1", "expires_at": future, "is_revoked": False},
        "hash-1",
        policy={"is_revoked": False}
    )
    assert res.is_valid

def test_verify_approver_authority_branches():
    # 1. Proposal owner approving own proposal
    res = verify_approver_authority("usr-alex-01", "usr-alex-01")
    assert res.is_valid
    assert "Primary account owner" in res.reason

    # 2. Third party with no guardian relationship
    res = verify_approver_authority("usr-stranger", "usr-alex-01", guardian_relationship=None)
    assert not res.is_valid
    assert "no registered guardian relationship" in res.reason

    # 3. Guardian without approval authority
    res = verify_approver_authority(
        "usr-alex-01",
        "usr-eleanor-02",
        guardian_relationship={"can_approve": False, "max_approval_amount_cents": 5000},
        total_cents=3000
    )
    assert not res.is_valid
    assert "does not possess delegated approval authority" in res.reason

    # 4. Guardian exceeding delegated limit
    res = verify_approver_authority(
        "usr-alex-01",
        "usr-eleanor-02",
        guardian_relationship={"can_approve": True, "max_approval_amount_cents": 5000},
        total_cents=7500
    )
    assert not res.is_valid
    assert "exceeds guardian's delegated limit" in res.reason

    # 5. Valid guardian within limit
    res = verify_approver_authority(
        "usr-alex-01",
        "usr-eleanor-02",
        guardian_relationship={"can_approve": True, "max_approval_amount_cents": 10000},
        total_cents=7500
    )
    assert res.is_valid
    assert "Delegated guardian authorization verified" in res.reason

# ==============================================================================
# 2. CARE Mode B Rules Comprehensive Verification
# ==============================================================================
def test_care_mode_b_product_whitelist_denial():
    policy = {
        "is_revoked": False,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "currency": "USD",
        "allowed_merchants": ["CVS Health Pharmacy"],
        "allowed_categories": ["Medical Supplies"],
        "allowed_products": ["prod-bp-cuff", "Blood Pressure Monitor"],
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 50000,
        "monthly_spent_cents": 0,
        "approval_mode": ApprovalMode.CONTROLLED_AUTO,
    }
    items = [{
        "product_id": "prod-unapproved-candy",
        "name": "Box of Chocolates",
        "category": "Medical Supplies",
        "quantity": 1,
        "unit_price_cents": 2500,
    }]
    eval_res = evaluate_transaction_policy(
        user_id="usr-eleanor-02",
        module=ModuleScope.CARE,
        merchant_id="m-cvs-01",
        merchant_name="CVS Health Pharmacy",
        items=items,
        total_cents=2500,
        currency="USD",
        fingerprint_hash="hash-1",
        computed_fingerprint_hash="hash-1",
        policy=policy,
        acting_user_id="usr-eleanor-02",
    )
    assert eval_res.decision == PolicyDecision.DENY
    assert "PRODUCT_NOT_APPROVED" in eval_res.failed_checks

def test_care_mode_b_quantity_restriction_denial():
    policy = {
        "is_revoked": False,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "currency": "USD",
        "allowed_merchants": ["CVS Health Pharmacy"],
        "allowed_categories": ["Medical Supplies"],
        "max_quantity_per_item": 2,
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 50000,
        "monthly_spent_cents": 0,
        "approval_mode": ApprovalMode.CONTROLLED_AUTO,
    }
    items = [{
        "product_id": "prod-test-strips",
        "name": "Glucose Test Strips 50ct",
        "category": "Medical Supplies",
        "quantity": 5, # Exceeds max 2
        "unit_price_cents": 1000,
    }]
    eval_res = evaluate_transaction_policy(
        user_id="usr-eleanor-02",
        module=ModuleScope.CARE,
        merchant_id="m-cvs-01",
        merchant_name="CVS Health Pharmacy",
        items=items,
        total_cents=5000,
        currency="USD",
        fingerprint_hash="hash-1",
        computed_fingerprint_hash="hash-1",
        policy=policy,
        acting_user_id="usr-eleanor-02",
    )
    assert eval_res.decision == PolicyDecision.DENY
    assert "QUANTITY_LIMIT_EXCEEDED" in eval_res.failed_checks

def test_care_mode_b_reorder_schedule_denial():
    policy = {
        "is_revoked": False,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "currency": "USD",
        "allowed_merchants": ["CVS Health Pharmacy"],
        "allowed_categories": ["Medical Supplies"],
        "requires_reorder_schedule": True,
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 50000,
        "monthly_spent_cents": 0,
        "approval_mode": ApprovalMode.CONTROLLED_AUTO,
    }
    items = [{
        "product_id": "prod-med-01",
        "name": "Senior Multivitamin",
        "category": "Medical Supplies",
        "quantity": 1,
        "unit_price_cents": 2500,
    }]
    # Inactive schedule
    eval_res = evaluate_transaction_policy(
        user_id="usr-eleanor-02",
        module=ModuleScope.CARE,
        merchant_id="m-cvs-01",
        merchant_name="CVS Health Pharmacy",
        items=items,
        total_cents=2500,
        currency="USD",
        fingerprint_hash="hash-1",
        computed_fingerprint_hash="hash-1",
        policy=policy,
        acting_user_id="usr-eleanor-02",
        reorder_schedule={"is_active": False},
    )
    assert eval_res.decision == PolicyDecision.DENY
    assert "REORDER_SCHEDULE_INACTIVE" in eval_res.failed_checks

def test_care_mode_b_price_tolerance_denial():
    policy = {
        "is_revoked": False,
        "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "currency": "USD",
        "allowed_merchants": ["CVS Health Pharmacy"],
        "allowed_categories": ["Medical Supplies"],
        "price_tolerance_pct": 10, # Max 10% increase over baseline
        "max_per_order_cents": 10000,
        "monthly_budget_cents": 50000,
        "monthly_spent_cents": 0,
        "approval_mode": ApprovalMode.CONTROLLED_AUTO,
    }
    items = [{
        "product_id": "prod-med-01",
        "name": "Senior Multivitamin",
        "category": "Medical Supplies",
        "quantity": 1,
        "unit_price_cents": 3500, # 40% higher than baseline $25.00
    }]
    eval_res = evaluate_transaction_policy(
        user_id="usr-eleanor-02",
        module=ModuleScope.CARE,
        merchant_id="m-cvs-01",
        merchant_name="CVS Health Pharmacy",
        items=items,
        total_cents=3500,
        currency="USD",
        fingerprint_hash="hash-1",
        computed_fingerprint_hash="hash-1",
        policy=policy,
        acting_user_id="usr-eleanor-02",
        baseline_item_prices={"prod-med-01": 2500},
    )
    assert eval_res.decision == PolicyDecision.DENY
    assert "PRICE_TOLERANCE_EXCEEDED" in eval_res.failed_checks

# ==============================================================================
# 3. Budget Concurrency & Atomic Spending Reservation Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_budget_concurrency_race_condition():
    """
    Proves that two concurrent requests attempting to spend remaining budget
    serialize atomically: one succeeds, and the other is denied due to ceiling breach.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a test policy with $100 monthly budget
        policy_res = await ac.post("/api/trust/policies", json={
            "user_id": "usr-alex-01",
            "scope": "PERSONAL",
            "title": "Concurrency Test Budget",
            "max_per_order_cents": 10000,
            "monthly_budget_cents": 10000, # $100.00
            "currency": "USD",
            "allowed_merchants": ["Keychron Official"],
            "allowed_categories": ["Peripherals"],
            "approval_mode": "EXPLICIT_ALWAYS",
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        })
        assert policy_res.status_code == 200
        policy_id = policy_res.json()["id"]

        # Attempt to reserve $60 concurrently twice (total $120 > $100 budget)
        async def reserve(amount: int):
            return await ac.post(
                f"/api/trust/policies/{policy_id}/reserve-budget?amount_cents={amount}"
            )

        res1, res2 = await asyncio.gather(reserve(6000), reserve(6000), return_exceptions=False)
        statuses = [res1.status_code, res2.status_code]

        # Exactly one must succeed (200), and exactly one must be rejected (400)
        assert 200 in statuses
        assert 400 in statuses

        # Verify the rejected one mentions budget denial
        failed_res = res1 if res1.status_code == 400 else res2
        assert "BUDGET DENIAL" in failed_res.json()["detail"]

# ==============================================================================
# 4. Authoritative Fingerprint Verification Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_client_spoofed_fingerprint_rejected():
    """
    Proves that a client submitting a spoofed fingerprint cannot override backend computation.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create proposal
        create_res = await ac.post("/api/trust/proposals", json={
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB Cable",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [{
                "product_id": "p-cable-1",
                "name": "Braided Cable",
                "quantity": 1,
                "unit_price_cents": 1500,
            }],
            "subtotal_cents": 1500,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Valid cable proposal.",
        })
        prop = create_res.json()
        proposal_id = prop["id"]

        # Transition to AWAITING_APPROVAL
        await ac.post(f"/api/trust/transactions/{proposal_id}/transition", json={
            "target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"
        })
        await ac.post(f"/api/trust/transactions/{proposal_id}/transition", json={
            "target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"
        })

        # Client attempts to approve with a fraudulent/spoofed hash
        spoofed_hash = "0000000000000000000000000000000000000000000000000000000000000000"
        appr_res = await ac.post("/api/trust/approve", json={
            "proposal_id": proposal_id,
            "fingerprint_hash": spoofed_hash,
            "approver_user_id": "usr-alex-01",
            "approval_type": "USER_DIRECT",
            "ttl_seconds": 3600,
        })
        assert appr_res.status_code == 400
        assert "Fingerprint mismatch" in appr_res.json()["detail"]

# ==============================================================================
# 5. Lifecycle Concurrency (Optimistic Locking) Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_lifecycle_optimistic_concurrency_conflict():
    """
    Proves that submitting a state transition with an outdated expected_version
    is rejected with a 409 Conflict.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_res = await ac.post("/api/trust/proposals", json={
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Concurrency Item",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [{
                "product_id": "p-item-c",
                "name": "Item C",
                "quantity": 1,
                "unit_price_cents": 2000,
            }],
            "subtotal_cents": 2000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Concurrency test proposal.",
        })
        prop = create_res.json()
        proposal_id = prop["id"]
        v1 = prop["version"]
        assert v1 == 1

        # Transition 1: advances version to 2
        t1 = await ac.post(f"/api/trust/transactions/{proposal_id}/transition", json={
            "target_state": "POLICY_CHECK",
            "actor_id": "usr-alex-01",
            "actor_role": "SYSTEM",
            "expected_version": 1,
        })
        assert t1.status_code == 200

        # Transition 2 with stale expected_version=1 must fail with 409 Conflict
        t2 = await ac.post(f"/api/trust/transactions/{proposal_id}/transition", json={
            "target_state": "AWAITING_APPROVAL",
            "actor_id": "usr-alex-01",
            "actor_role": "SYSTEM",
            "expected_version": 1, # Stale version
        })
        assert t2.status_code == 409
        assert "Optimistic concurrency conflict" in t2.json()["detail"]
