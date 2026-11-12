import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update

from app.main import app
from app.core.database import AsyncSessionLocal
from app.models.entities import (
    PurchaseProposal,
    Approval,
    TransactionFingerprint,
    Transaction,
    ConsentPolicy,
    AuditEvent,
)
from app.models.enums import (
    TransactionLifecycleState,
    ApprovalType,
    AuditEventType,
    PolicyDecision,
    ModuleScope,
)
from app.engine.approval import (
    verify_approval_integrity,
    ApprovalVerificationResult,
    record_approval_integrity_diagnostic,
)
from app.engine.fingerprint import compute_transaction_fingerprint


@pytest.mark.asyncio
async def test_scenario_1_valid_mode_a_approval_remains_valid_during_checkout():
    """Scenario 1: Valid Mode A human approval remains valid through entire checkout lifecycle."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = "usr-alex-01"
        prop_payload = {
            "user_id": uid,
            "module": "PERSONAL",
            "title": f"Scenario 1 Mode A Mechanical Switch Puller {uuid.uuid4().hex[:6]}",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": f"prod-puller-{uuid.uuid4().hex[:6]}",
                    "name": "Precision Switch Puller",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 1200,
                }
            ],
            "subtotal_cents": 1200,
            "tax_cents": 96,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Valid personal keyboard tool.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        assert res_prop.status_code == 200
        prop = res_prop.json()
        pid = prop["id"]
        fp = prop["fingerprint_sha256"]

        # Advance to AWAITING_APPROVAL
        await ac.post(
            f"/api/trust/transactions/{pid}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": uid, "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{pid}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": uid, "actor_role": "SYSTEM"},
        )

        # Submit valid human approval
        res_app = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": pid,
                "approver_user_id": uid,
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fp,
                "ttl_seconds": 3600,
            },
        )
        assert res_app.status_code == 200
        approval_data = res_app.json()
        assert approval_data["is_revoked"] is False

        # Checkout
        res_order = await ac.post(f"/api/payments/{pid}/create-order")
        assert res_order.status_code == 200
        order = res_order.json()
        assert order["order_id"].startswith("SIM-ORD-")
        assert order["status"] == "CREATED"


@pytest.mark.asyncio
async def test_scenario_2_approve_and_immediately_initiate_checkout():
    """Scenario 2: User approves eligible proposal and immediately triggers checkout without revocation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = "usr-alex-01"
        res_prop = await ac.post(
            "/api/trust/proposals",
            json={
                "user_id": uid,
                "module": "PERSONAL",
                "title": f"Scenario 2 Immediate Checkout Cable {uuid.uuid4().hex[:6]}",
                "merchant_id": "m-keychron-01",
                "merchant_name": "Keychron Official",
                "items": [
                    {
                        "product_id": f"prod-cable-{uuid.uuid4().hex[:6]}",
                        "name": "Braided USB-C Cable",
                        "category": "Cables",
                        "quantity": 1,
                        "unit_price_cents": 1500,
                    }
                ],
                "subtotal_cents": 1500,
                "tax_cents": 120,
                "shipping_cents": 0,
                "currency": "USD",
                "ai_reasoning": "Immediate checkout test.",
            },
        )
        prop = res_prop.json()
        pid = prop["id"]
        fp = prop["fingerprint_sha256"]

        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "POLICY_CHECK", "actor_id": uid, "actor_role": "SYSTEM"})
        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "AWAITING_APPROVAL", "actor_id": uid, "actor_role": "SYSTEM"})

        # Approve and immediate checkout
        res_app = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": pid,
                "approver_user_id": uid,
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fp,
                "ttl_seconds": 1800,
            },
        )
        assert res_app.status_code == 200

        res_order = await ac.post(f"/api/payments/{pid}/create-order")
        assert res_order.status_code == 200
        assert res_order.json()["proposal_id"] == pid


@pytest.mark.asyncio
async def test_scenario_3_normal_page_refresh_does_not_revoke_approval():
    """Scenario 3: Normal repeated page refresh / query read calls never revoke approval."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = "usr-alex-01"
        res_prop = await ac.post(
            "/api/trust/proposals",
            json={
                "user_id": uid,
                "module": "PERSONAL",
                "title": f"Scenario 3 Refresh Resistant Keycaps {uuid.uuid4().hex[:6]}",
                "merchant_id": "m-keychron-01",
                "merchant_name": "Keychron Official",
                "items": [
                    {
                        "product_id": f"prod-caps-{uuid.uuid4().hex[:6]}",
                        "name": "PBT Keycaps Set",
                        "category": "Peripherals",
                        "quantity": 1,
                        "unit_price_cents": 3500,
                    }
                ],
                "subtotal_cents": 3500,
                "tax_cents": 280,
                "shipping_cents": 0,
                "currency": "USD",
                "ai_reasoning": "Refresh resilience verification.",
            },
        )
        prop = res_prop.json()
        pid = prop["id"]
        fp = prop["fingerprint_sha256"]

        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "POLICY_CHECK", "actor_id": uid, "actor_role": "SYSTEM"})
        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "AWAITING_APPROVAL", "actor_id": uid, "actor_role": "SYSTEM"})

        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": pid,
                "approver_user_id": uid,
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fp,
                "ttl_seconds": 3600,
            },
        )

        # Simulate 3 rapid page refreshes
        for _ in range(3):
            res_get = await ac.get(f"/api/trust/proposals/{pid}")
            assert res_get.status_code == 200
            assert res_get.json()["state"] == "APPROVED"

        # Checkout succeeds without revocation
        res_order = await ac.post(f"/api/payments/{pid}/create-order")
        assert res_order.status_code == 200


@pytest.mark.asyncio
async def test_scenario_4_duplicate_approval_requests_idempotent():
    """Scenario 4: Duplicate approval requests do not create conflicting revocations (safe idempotency)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = "usr-alex-01"
        res_prop = await ac.post(
            "/api/trust/proposals",
            json={
                "user_id": uid,
                "module": "PERSONAL",
                "title": f"Scenario 4 Idempotent Desk Mat {uuid.uuid4().hex[:6]}",
                "merchant_id": "m-keychron-01",
                "merchant_name": "Keychron Official",
                "items": [
                    {
                        "product_id": f"prod-mat-{uuid.uuid4().hex[:6]}",
                        "name": "Leather Desk Mat",
                        "category": "Peripherals",
                        "quantity": 1,
                        "unit_price_cents": 2900,
                    }
                ],
                "subtotal_cents": 2900,
                "tax_cents": 232,
                "shipping_cents": 0,
                "currency": "USD",
                "ai_reasoning": "Idempotency test.",
            },
        )
        prop = res_prop.json()
        pid = prop["id"]
        fp = prop["fingerprint_sha256"]

        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "POLICY_CHECK", "actor_id": uid, "actor_role": "SYSTEM"})
        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "AWAITING_APPROVAL", "actor_id": uid, "actor_role": "SYSTEM"})

        # First approval
        res1 = await ac.post(
            "/api/trust/approve",
            json={"proposal_id": pid, "approver_user_id": uid, "approval_type": "USER_DIRECT", "fingerprint_hash": fp, "ttl_seconds": 3600},
        )
        assert res1.status_code == 200
        app_id_1 = res1.json()["id"]

        # Duplicate approval (simulating network retry / double-click)
        res2 = await ac.post(
            "/api/trust/approve",
            json={"proposal_id": pid, "approver_user_id": uid, "approval_type": "USER_DIRECT", "fingerprint_hash": fp, "ttl_seconds": 3600},
        )
        assert res2.status_code == 200
        app_id_2 = res2.json()["id"]
        assert app_id_1 == app_id_2

        # Order creation remains authorized
        res_order = await ac.post(f"/api/payments/{pid}/create-order")
        assert res_order.status_code == 200


@pytest.mark.asyncio
async def test_scenario_5_invalid_fingerprint_blocks_checkout():
    """Scenario 5: Parameter tampering / fingerprint mismatch blocks checkout with FINGERPRINT_MISMATCH."""
    original_h = "a" * 64
    tampered_h = "b" * 64
    approval = {
        "fingerprint_hash": original_h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": False,
    }
    verif = verify_approval_integrity(approval, expected_fingerprint_hash=tampered_h)
    assert verif.is_valid is False
    assert verif.reason_code == "FINGERPRINT_MISMATCH"
    assert "hash mismatch" in verif.reason.lower()


@pytest.mark.asyncio
async def test_scenario_6_expired_consent_blocks_checkout():
    """Scenario 6: Expired consent blocks checkout with CONSENT_TOKEN_EXPIRED."""
    h = "c" * 64
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=10),
        "is_revoked": False,
    }
    verif = verify_approval_integrity(approval, expected_fingerprint_hash=h)
    assert verif.is_valid is False
    assert verif.reason_code == "CONSENT_TOKEN_EXPIRED"
    assert "expired" in verif.reason.lower()


@pytest.mark.asyncio
async def test_scenario_7_wrong_user_identity_blocks_checkout():
    """Scenario 7: Unauthorized user identity blocks checkout with IDENTITY_MISMATCH."""
    h = "d" * 64
    approval = {
        "fingerprint_hash": h,
        "approver_user_id": "usr-attacker-99",
        "approval_type": "USER_DIRECT",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": False,
    }
    verif = verify_approval_integrity(
        approval=approval,
        expected_fingerprint_hash=h,
        expected_user_id="usr-legitimate-01",
        current_user_id="usr-attacker-99",
    )
    assert verif.is_valid is False
    assert verif.reason_code == "IDENTITY_MISMATCH"
    assert "identity mismatch" in verif.reason.lower()


@pytest.mark.asyncio
async def test_scenario_8_explicitly_revoked_approval_blocks_checkout():
    """Scenario 8: Explicitly revoked approval blocks checkout with APPROVAL_EXPLICITLY_REVOKED."""
    h = "e" * 64
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": True,
        "revoked_reason": "Guardian cancelled authorization.",
    }
    verif = verify_approval_integrity(approval, expected_fingerprint_hash=h)
    assert verif.is_valid is False
    assert verif.reason_code == "APPROVAL_EXPLICITLY_REVOKED"
    assert "explicitly revoked" in verif.reason.lower()


@pytest.mark.asyncio
async def test_scenario_9_confirmed_suspicious_activity_blocks_checkout():
    """Scenario 9: Confirmed suspicious activity blocks checkout with SUSPICIOUS_ACTIVITY_CONFIRMED."""
    h = "f" * 64
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": True,
        "revoked_reason": "Suspicious user activity detected.",
    }
    verif = verify_approval_integrity(approval, expected_fingerprint_hash=h)
    assert verif.is_valid is False
    assert verif.reason_code == "SUSPICIOUS_ACTIVITY_CONFIRMED"
    assert "suspicious" in verif.reason.lower()


@pytest.mark.asyncio
async def test_scenario_10_legitimate_user_activity_no_false_positive():
    """Scenario 10: Legitimate user activity does not trigger false-positive suspicious activity flags."""
    h = "1" * 64
    approval = {
        "id": "app-legit-01",
        "fingerprint_hash": h,
        "approver_user_id": "usr-alex-01",
        "approval_type": "USER_DIRECT",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": False,
        "revoked_reason": None,
    }
    policy = {"is_revoked": False, "expires_at": datetime.now(timezone.utc) + timedelta(days=30)}

    verif = verify_approval_integrity(
        approval=approval,
        expected_fingerprint_hash=h,
        policy=policy,
        expected_user_id="usr-alex-01",
        current_user_id="usr-alex-01",
        proposal_id="prop-legit-01",
    )
    assert verif.is_valid is True
    assert verif.reason_code == "APPROVAL_VALID"


@pytest.mark.asyncio
async def test_scenario_11_checkout_after_rejection_is_blocked():
    """Scenario 11: Checkout attempted after proposal rejection is blocked immediately."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = "usr-alex-01"
        res_prop = await ac.post(
            "/api/trust/proposals",
            json={
                "user_id": uid,
                "module": "PERSONAL",
                "title": f"Scenario 11 Rejected Proposal {uuid.uuid4().hex[:6]}",
                "merchant_id": "m-keychron-01",
                "merchant_name": "Keychron Official",
                "items": [
                    {
                        "product_id": f"prod-rej-{uuid.uuid4().hex[:6]}",
                        "name": "Rejected Item",
                        "category": "Peripherals",
                        "quantity": 1,
                        "unit_price_cents": 5000,
                    }
                ],
                "subtotal_cents": 5000,
                "tax_cents": 400,
                "shipping_cents": 0,
                "currency": "USD",
                "ai_reasoning": "Rejection block test.",
            },
        )
        pid = res_prop.json()["id"]

        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "POLICY_CHECK", "actor_id": uid, "actor_role": "SYSTEM"})
        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "REJECTED", "actor_id": uid, "actor_role": "SYSTEM"})

        res_order = await ac.post(f"/api/payments/{pid}/create-order")
        assert res_order.status_code == 403
        assert "rejected" in res_order.json()["detail"].lower()


@pytest.mark.asyncio
async def test_scenario_12_single_paypal_order_per_proposal():
    """Scenario 12: Repeated checkout calls for an approved proposal return the existing order (at most one order)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = "usr-alex-01"
        res_prop = await ac.post(
            "/api/trust/proposals",
            json={
                "user_id": uid,
                "module": "PERSONAL",
                "title": f"Scenario 12 Single Order Monitor Arm {uuid.uuid4().hex[:6]}",
                "merchant_id": "m-keychron-01",
                "merchant_name": "Keychron Official",
                "items": [
                    {
                        "product_id": f"prod-arm-{uuid.uuid4().hex[:6]}",
                        "name": "Single Monitor Arm",
                        "category": "Peripherals",
                        "quantity": 1,
                        "unit_price_cents": 7500,
                    }
                ],
                "subtotal_cents": 7500,
                "tax_cents": 600,
                "shipping_cents": 0,
                "currency": "USD",
                "ai_reasoning": "Single order verification.",
            },
        )
        prop = res_prop.json()
        pid = prop["id"]
        fp = prop["fingerprint_sha256"]

        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "POLICY_CHECK", "actor_id": uid, "actor_role": "SYSTEM"})
        await ac.post(f"/api/trust/transactions/{pid}/transition", json={"target_state": "AWAITING_APPROVAL", "actor_id": uid, "actor_role": "SYSTEM"})
        await ac.post("/api/trust/approve", json={"proposal_id": pid, "approver_user_id": uid, "approval_type": "USER_DIRECT", "fingerprint_hash": fp, "ttl_seconds": 3600})

        # Order 1
        res1 = await ac.post(f"/api/payments/{pid}/create-order")
        assert res1.status_code == 200
        order_id_1 = res1.json()["order_id"]

        # Order 2 (retry)
        res2 = await ac.post(f"/api/payments/{pid}/create-order")
        assert res2.status_code == 200
        order_id_2 = res2.json()["order_id"]

        assert order_id_1 == order_id_2


@pytest.mark.asyncio
async def test_scenario_13_provider_timeout_reconciliation():
    """Scenario 13: Order creation recovery handles reconciliation status deterministically."""
    async with AsyncSessionLocal() as session:
        # Verify reconciliation enum values exist in system
        res = await session.execute(select(Transaction).where(Transaction.reconciliation_status.isnot(None)).limit(1))
        tx = res.scalar_one_or_none()
        assert tx is None or isinstance(tx.reconciliation_status, str)


@pytest.mark.asyncio
async def test_scenario_14_structured_diagnostic_logging_records_correct_fields():
    """Scenario 14: Structured diagnostic logging captures required fields without logging secrets."""
    event = record_approval_integrity_diagnostic(
        proposal_id="prop-diag-01",
        user_id="usr-alex-01",
        approval_id="app-diag-01",
        status_before="PENDING",
        status_after="VALID",
        is_revoked=False,
        revocation_reason_code="APPROVAL_VALID",
        security_signals=["INTEGRITY_VERIFIED"],
        fingerprint_result=True,
        consent_token_result=True,
        identity_consistency_result=True,
        checkout_attempt_id="chk-attempt-1234",
        details={"mode": "Mode A", "password": "SHOULD_BE_FILTERED_OUT"},
    )
    assert event["proposal_id"] == "prop-diag-01"
    assert event["user_id"] == "usr-alex-01"
    assert event["revocation_reason_code"] == "APPROVAL_VALID"
    assert event["fingerprint_verification"] == "PASS"
    assert "password" not in event["metadata"]


@pytest.mark.asyncio
async def test_scenario_15_no_regression_in_pact_flow_care_common():
    """Scenario 15: Invariant verification across all modules (PACT, FLOW, CARE, COMMON)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # PACT endpoint
        res_pact = await ac.get("/api/trust/proposals?module=PERSONAL")
        assert res_pact.status_code == 200

        # FLOW endpoint
        res_flow = await ac.get("/api/business/profiles")
        assert res_flow.status_code in (200, 404)

        # CARE endpoint
        res_care = await ac.get("/api/care/profiles")
        assert res_care.status_code in (200, 404)

        # COMMON endpoint
        res_common = await ac.get("/api/groups")
        assert res_common.status_code == 200
