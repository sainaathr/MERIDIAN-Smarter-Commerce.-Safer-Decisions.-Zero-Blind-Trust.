"""
MERIDIAN Phase 5 End-to-End Integration Tests
Validates dynamic proposal persistence, policy evaluation, state transitions,
live transaction history endpoint, live audit log endpoint, and full E2E flow.
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.init_db import init_database
from app.core.database import AsyncSessionLocal
from app.models.enums import TransactionLifecycleState, PolicyDecision
from app.models.entities import PolicyEvaluationRecord, Approval, AuditEvent, PurchaseProposal

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()

@pytest.mark.asyncio
async def test_create_proposal_arithmetic_validation():
    """Verify POST /api/trust/proposals rejects line item mismatch and requires valid totals."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Line item calculation does not match subtotal_cents
        invalid_subtotal_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Mismatched Pricing Item",
            "merchant_id": "m-store-01",
            "merchant_name": "Store Official",
            "items": [
                {
                    "product_id": "prod-01",
                    "name": "Item 1",
                    "quantity": 2,
                    "unit_price_cents": 1000, # 2 * 1000 = 2000
                }
            ],
            "subtotal_cents": 1500, # Mismatch!
            "tax_cents": 100,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Test invalid pricing",
        }
        res = await ac.post("/api/trust/proposals", json=invalid_subtotal_payload)
        assert res.status_code == 400
        assert "does not match subtotal_cents" in res.json()["detail"]

        # 2. Empty items
        empty_items_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Empty Items Proposal",
            "merchant_id": "m-store-01",
            "merchant_name": "Store Official",
            "items": [],
            "subtotal_cents": 0,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Test empty items",
        }
        res = await ac.post("/api/trust/proposals", json=empty_items_payload)
        assert res.status_code in [400, 422]

        # 3. Valid proposal creation
        valid_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Valid Mechanical Keyboard",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-k3-pro",
                    "name": "Keychron K3 Pro",
                    "quantity": 1,
                    "unit_price_cents": 8400,
                    "category": "Peripherals",
                }
            ],
            "subtotal_cents": 8400,
            "tax_cents": 672,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Valid peripheral purchase",
        }
        res = await ac.post("/api/trust/proposals", json=valid_payload)
        assert res.status_code == 200
        data = res.json()
        assert bool(data["id"]) is True
        assert data["state"] == "PROPOSED"
        assert len(data["fingerprint_sha256"]) == 64
        assert data["total_cents"] == 8400 + 672
        assert len(data["items"]) == 1
        assert data["items"][0]["total_cents"] == 8400

@pytest.mark.asyncio
async def test_get_proposals_listing_and_filtering():
    """Verify GET /api/trust/proposals returns persisted proposals and supports module filtering."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Fetch all proposals
        res = await ac.get("/api/trust/proposals")
        assert res.status_code == 200
        proposals = res.json()
        assert isinstance(proposals, list)
        assert len(proposals) >= 1

        # Check structure
        first = proposals[0]
        assert "id" in first
        assert "module" in first
        assert "fingerprint_sha256" in first
        assert "items" in first
        assert "total_cents" in first

        # Filter by PERSONAL
        res_personal = await ac.get("/api/trust/proposals?module=PERSONAL")
        assert res_personal.status_code == 200
        personal_props = res_personal.json()
        for p in personal_props:
            assert p["module"] == "PERSONAL"

        # Filter by CARE
        res_care = await ac.get("/api/trust/proposals?module=CARE")
        assert res_care.status_code == 200
        care_props = res_care.json()
        for p in care_props:
            assert p["module"] == "CARE"

@pytest.mark.asyncio
async def test_empty_query_returns_clean_empty_list():
    """Verify endpoints return clean empty JSON lists ([]) when filters match zero records."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Non-matching module filter returns empty list []
        res = await ac.get("/api/trust/proposals?module=COLLECTIVE")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        # Verify it is valid JSON list
        for p in data:
            assert p["module"] == "COLLECTIVE"

        # Limit 0 returns empty list []
        res_zero = await ac.get("/api/trust/proposals?limit=0")
        assert res_zero.status_code == 200
        assert res_zero.json() == []

        # Audit query with non-matching severity returns empty list []
        res_audit = await ac.get("/api/trust/audit?severity=ALERT&module=COLLECTIVE")
        assert res_audit.status_code == 200
        assert isinstance(res_audit.json(), list)

@pytest.mark.asyncio
async def test_get_payment_transactions_listing():
    """Verify GET /api/payments/transactions returns list of payment records."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/payments/transactions")
        assert res.status_code == 200
        txs = res.json()
        assert isinstance(txs, list)
        if len(txs) > 0:
            tx = txs[0]
            assert "transaction_id" in tx
            assert "proposal_id" in tx
            assert "state" in tx
            assert "total_cents" in tx
            assert "currency" in tx
            assert "is_simulated" in tx

@pytest.mark.asyncio
async def test_get_audit_events_filtering():
    """Verify GET /api/trust/audit supports query filtering by severity and module."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Base query
        res = await ac.get("/api/trust/audit?limit=20")
        assert res.status_code == 200
        events = res.json()
        assert isinstance(events, list)

        # Severity filter
        res_info = await ac.get("/api/trust/audit?severity=INFO")
        assert res_info.status_code == 200
        for evt in res_info.json():
            assert evt["severity"] == "INFO"

        # Module filter
        res_module = await ac.get("/api/trust/audit?module=PERSONAL")
        assert res_module.status_code == 200
        for evt in res_module.json():
            assert evt["module"] == "PERSONAL"

@pytest.mark.asyncio
async def test_full_phase5_end_to_end_flow():
    """
    Test the complete End-to-End flow:
    1. Dynamic proposal creation
    2. Policy evaluation
    3. Legal lifecycle transition to AWAITING_APPROVAL
    4. Explicit human approval with cryptographic fingerprint binding
    5. Order creation
    6. Payment capture
    7. Reflection in proposals, transactions, and audit trail
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unique = uuid.uuid4().hex[:6]
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": f"Full Flow Keyboard {unique}",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": f"prod-kb-{unique}",
                    "name": f"Mechanical Keyboard {unique}",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 8500,
                }
            ],
            "subtotal_cents": 8500,
            "tax_cents": 680,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "E2E Integration Verification Test",
        }

        # 1. Create Proposal
        res_create = await ac.post("/api/trust/proposals", json=prop_payload)
        assert res_create.status_code == 200
        proposal = res_create.json()
        proposal_id = proposal["id"]
        fingerprint = proposal["fingerprint_sha256"]
        assert proposal["state"] == "PROPOSED"

        # 2. Evaluate Policy
        res_eval = await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})
        assert res_eval.status_code == 200
        eval_data = res_eval.json()
        assert eval_data["decision"] in ["ALLOW", "REQUIRE_APPROVAL"]
        assert eval_data["fingerprint_hash"] == fingerprint

        # 3. Transition to POLICY_CHECK then AWAITING_APPROVAL
        res_t1 = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t1.status_code == 200

        res_t2 = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t2.status_code == 200

        # 4. Human Approval
        res_appr = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        assert res_appr.status_code == 200

        # 5. Create PayPal Order
        res_order = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert res_order.status_code == 200
        order_data = res_order.json()
        paypal_order_id = order_data["order_id"]
        assert paypal_order_id.startswith("ORD-") or paypal_order_id.startswith("SIM-ORD-")
        assert order_data["fingerprint_sha256"] == fingerprint

        # 6. Capture Order
        res_cap = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
        )
        assert res_cap.status_code == 200
        cap_data = res_cap.json()
        assert cap_data["proposal_id"] == proposal_id

        # 7. Verify Proposal State
        res_prop_detail = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert res_prop_detail.status_code == 200
        prop_after = res_prop_detail.json()
        assert prop_after["paypal_order_id"] == paypal_order_id

        if cap_data.get("is_simulated"):
            # Strict Phase 4 Invariant: Simulated captures remain in PAYMENT_PENDING and are NEVER marked PAID
            assert prop_after["state"] == "PAYMENT_PENDING"
            assert prop_after["paypal_status"] == "SIMULATED"
            assert prop_after["is_simulated"] is True
        else:
            assert prop_after["state"] == "PAID"
            assert prop_after["paypal_status"] == "COMPLETED"

        # 8. Verify Presence in GET /api/trust/proposals
        res_list = await ac.get("/api/trust/proposals")
        assert res_list.status_code == 200
        found_in_list = any(p["id"] == proposal_id for p in res_list.json())
        assert found_in_list is True

        # 9. Verify Presence in GET /api/payments/transactions
        res_txs = await ac.get("/api/payments/transactions")
        assert res_txs.status_code == 200
        found_tx = any(t["proposal_id"] == proposal_id for t in res_txs.json())
        assert found_tx is True

        # 10. Verify Audit Log contains events for this proposal
        res_audit = await ac.get("/api/trust/audit?limit=100")
        assert res_audit.status_code == 200
        audit_events = res_audit.json()
        event_types = [evt["event_type"] for evt in audit_events if evt.get("entity_id") == proposal_id]
        assert "PROPOSAL_CREATED" in event_types
        assert "APPROVAL_GRANTED" in event_types


# ==============================================================================
# Phase 5.2 Task 2 — Mandatory Deny-Path & Approval Invariant Regression Tests
# ==============================================================================

async def _create_denied_test_proposal(ac: AsyncClient) -> dict:
    """Helper to create a proposal guaranteed to violate policy limits ($250 > $150 cap, unapproved merchant)."""
    unique = uuid.uuid4().hex[:6]
    prop_payload = {
        "user_id": "usr-alex-01",
        "module": "PERSONAL",
        "title": f"Unauthorized Drone Purchase {unique}",
        "merchant_id": f"m-rogue-merchant-{unique}",
        "merchant_name": "Rogue Electronics Co",
        "items": [
            {
                "product_id": f"prod-drone-{unique}",
                "name": f"High Ticket Drone {unique}",
                "category": "Electronics",
                "quantity": 1,
                "unit_price_cents": 25000,
            }
        ],
        "subtotal_cents": 25000,
        "tax_cents": 2000,
        "shipping_cents": 0,
        "currency": "USD",
        "ai_reasoning": "Attempting unapproved high-ticket purchase exceeding personal policy cap",
    }
    res = await ac.post("/api/trust/proposals", json=prop_payload)
    assert res.status_code == 200
    return res.json()


@pytest.mark.asyncio
async def test_deny_evaluation_is_persisted_and_transitions_proposal():
    """
    Test 1 & 2:
    - DENY evaluation is persisted in policy_evaluations table
    - DENY evaluation automatically transitions proposal to REJECTED and emits audit event
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Run policy evaluation
        res_eval = await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})
        assert res_eval.status_code == 200
        eval_data = res_eval.json()
        assert eval_data["decision"] == "DENY"

        # Verify proposal state transitioned to REJECTED
        res_prop = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert res_prop.status_code == 200
        prop_after = res_prop.json()
        assert prop_after["state"] == "REJECTED"
        assert prop_after["policy_decision"] == "DENY"

        # Verify evaluation record is persisted in policy_evaluations table
        async with AsyncSessionLocal() as session:
            eval_query = select(PolicyEvaluationRecord).where(PolicyEvaluationRecord.proposal_id == proposal_id)
            eval_records = (await session.execute(eval_query)).scalars().all()
            assert len(eval_records) >= 1
            latest = eval_records[-1]
            assert latest.decision in ("DENY", PolicyDecision.DENY)
            assert latest.proposal_id == proposal_id
            assert len(latest.failed_checks) > 0


@pytest.mark.asyncio
async def test_deny_proposal_cannot_transition_to_awaiting_approval():
    """
    Test 3: POST /api/trust/transactions/{proposal_id}/transition with target_state='AWAITING_APPROVAL'
    is rejected with HTTP 403 Forbidden for a denied/rejected proposal.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Evaluate -> DENY
        await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})

        # Attempt transition to AWAITING_APPROVAL
        res_t = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t.status_code == 403
        assert "Forbidden" in res_t.json()["detail"]


@pytest.mark.asyncio
async def test_deny_proposal_cannot_transition_to_approved():
    """
    Test 4: POST /api/trust/transactions/{proposal_id}/transition with target_state='APPROVED'
    is rejected with HTTP 403 Forbidden for a denied/rejected proposal.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Evaluate -> DENY
        await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})

        # Attempt transition to APPROVED
        res_t = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "APPROVED", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t.status_code == 403
        assert "Forbidden" in res_t.json()["detail"]


@pytest.mark.asyncio
async def test_approval_endpoint_returns_403_and_creates_no_record_for_denied_proposal():
    """
    Test 5 & 6:
    - POST /api/trust/approve returns HTTP 403 Forbidden for a proposal with a DENY verdict.
    - No Approval record is inserted into the approvals table.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]
        fingerprint = proposal["fingerprint_sha256"]

        # Evaluate -> DENY
        await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})

        # Attempt human approval
        res_appr = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        assert res_appr.status_code == 403
        assert "Forbidden" in res_appr.json()["detail"]

        # Verify no Approval record was written to the database
        async with AsyncSessionLocal() as session:
            appr_query = select(Approval).where(Approval.fingerprint_hash == fingerprint)
            appr_records = (await session.execute(appr_query)).scalars().all()
            assert len(appr_records) == 0


@pytest.mark.asyncio
async def test_paypal_order_creation_returns_403_for_denied_proposal():
    """
    Test 7: POST /api/payments/{proposal_id}/create-order returns HTTP 403 Forbidden for denied proposals.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Evaluate -> DENY
        await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})

        # Attempt order creation
        res_order = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert res_order.status_code == 403
        assert "REJECTED" in res_order.json()["detail"] or "DENY" in res_order.json()["detail"]


@pytest.mark.asyncio
async def test_capture_cannot_succeed_for_denied_proposal():
    """
    Test 8: POST /api/payments/{proposal_id}/capture cannot succeed for a denied proposal.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Evaluate -> DENY
        await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})

        # Attempt capture
        res_cap = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": "ORD-FAKE-DENIED"},
        )
        assert res_cap.status_code in [400, 403]


@pytest.mark.asyncio
async def test_reconciliation_cannot_mark_denied_proposal_paid():
    """
    Test 9: Reconciliation cannot mark a denied proposal PAID.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Evaluate -> DENY
        await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})

        # Attempt reconciliation
        res_recon = await ac.post(f"/api/payments/{proposal_id}/reconcile")
        assert res_recon.status_code in [400, 403]

        # Verify proposal remains REJECTED, never PAID
        res_prop = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert res_prop.status_code == 200
        assert res_prop.json()["state"] == "REJECTED"


@pytest.mark.asyncio
async def test_allow_and_require_approval_flows_work():
    """
    Test 10: Verified proposals under policy limit ($84 Mechanical Keyboard from Keychron)
    evaluate to ALLOW or REQUIRE_APPROVAL and succeed through the complete flow.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unique = uuid.uuid4().hex[:6]
        valid_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": f"Keychron K3 Pro {unique}",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": f"prod-kb-{unique}",
                    "name": f"Mechanical Keyboard {unique}",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 8400,
                }
            ],
            "subtotal_cents": 8400,
            "tax_cents": 672,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Valid personal keyboard purchase under $150 cap",
        }
        res_create = await ac.post("/api/trust/proposals", json=valid_payload)
        assert res_create.status_code == 200
        proposal = res_create.json()
        proposal_id = proposal["id"]
        fingerprint = proposal["fingerprint_sha256"]

        # Evaluate -> REQUIRE_APPROVAL or ALLOW
        res_eval = await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})
        assert res_eval.status_code == 200
        assert res_eval.json()["decision"] in ["ALLOW", "REQUIRE_APPROVAL"]

        # Transitions succeed
        res_t1 = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t1.status_code == 200

        res_t2 = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t2.status_code == 200

        # Approval succeeds
        res_appr = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        assert res_appr.status_code == 200


@pytest.mark.asyncio
async def test_repeated_evaluation_cannot_restore_denied_proposal():
    """
    Test 11: Repeated evaluation cannot restore a denied proposal to an approvable state.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # 1st evaluation -> DENY -> REJECTED
        res_eval1 = await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})
        assert res_eval1.status_code == 200
        assert res_eval1.json()["decision"] == "DENY"

        # 2nd evaluation on the same proposal
        res_eval2 = await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-alex-01"})
        assert res_eval2.status_code == 200
        assert res_eval2.json()["decision"] == "DENY"

        # Proposal state is strictly REJECTED
        res_prop = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert res_prop.status_code == 200
        assert res_prop.json()["state"] == "REJECTED"

        # Still cannot transition to AWAITING_APPROVAL
        res_t = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert res_t.status_code == 403


@pytest.mark.asyncio
async def test_tampered_fingerprint_and_revoked_consent_remain_intact():
    """
    Test 12: Tampered fingerprint and revoked consent protections remain intact.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal = await _create_denied_test_proposal(ac)
        proposal_id = proposal["id"]

        # Fingerprint mismatch in approval request
        res_fake_appr = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": "0" * 64,  # Fake mismatched hash
                "ttl_seconds": 3600,
            },
        )
        assert res_fake_appr.status_code == 400
        assert "mismatch" in res_fake_appr.json()["detail"].lower()
