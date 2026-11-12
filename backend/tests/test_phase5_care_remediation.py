"""
MERIDIAN — Phase 5.3 Task 1 Security Remediation Verification Suite
Tests the four critical audit corrections:
1. Finding 1: Mode A approver authority, guardian relationship, spending ceilings, category permissions, and consent revalidation.
2. Finding 2: Total isolation between simulated financial accounting and authoritative genuine expenditure.
3. Finding 3: Atomic CARE budget reservations, concurrency race condition mitigation, and idempotency.
4. Finding 4: Consistent generic CARE policy evaluation with persisted schedules, medical safety, and baseline prices.
"""

import uuid
import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update

from app.main import app
from app.core.init_db import init_database
from app.core.database import AsyncSessionLocal
from app.models.enums import (
    TransactionLifecycleState,
    PolicyDecision,
    ApprovalMode,
    ApprovalType,
    ModuleScope,
    ReservationStatus,
)
from app.models.entities import (
    User,
    CareProfile,
    GuardianRelationship,
    DelegatedPermission,
    ConsentPolicy,
    ReorderSchedule,
    PurchaseProposal,
    Approval,
    PolicyEvaluationRecord,
    BudgetReservation,
    Transaction,
    TransactionFingerprint,
)
from app.engine.budget import reserve_care_budget, consume_care_budget, release_care_budget, process_expired_reservations_and_proposals
from app.engine.fingerprint import compute_transaction_fingerprint
from app.payments.paypal_service import PayPalService
from app.payments.exceptions import PaymentCaptureError, PaymentVerificationError
from unittest.mock import patch, AsyncMock


@pytest_asyncio.fixture(autouse=True)
async def reset_remediation_baseline():
    """Ensures consistent database baseline before each test."""
    await init_database()
    async with AsyncSessionLocal() as session:
        # Reset Eleanor's profile: $120.00 spent, $0.00 reserved, Mode A
        await session.execute(
            update(CareProfile)
            .where(CareProfile.id == "care-eleanor-01")
            .values(
                monthly_spent_cents=12000,
                reserved_cents=0,
                simulated_monthly_spent_cents=0,
                approval_mode=ApprovalMode.EXPLICIT_ALWAYS,
            )
        )
        await session.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.user_id == "usr-eleanor-02", ConsentPolicy.scope == ModuleScope.CARE)
            .values(
                monthly_spent_cents=12000,
                reserved_cents=0,
                simulated_monthly_spent_cents=0,
                approval_mode=ApprovalMode.EXPLICIT_ALWAYS,
                is_revoked=False,
            )
        )
        # Ensure Alex Rivera is active guardian with $150.00 ceiling and can_approve=True
        await session.execute(
            update(GuardianRelationship)
            .where(GuardianRelationship.guardian_user_id == "usr-alex-01")
            .values(can_approve=True, max_approval_amount_cents=15000, is_active=True)
        )
        # Ensure seeded schedules are active and eligible
        await session.execute(
            update(ReorderSchedule)
            .where(ReorderSchedule.id.in_(["sched-cgm-01", "sched-vit-02", "sched-cream-03"]))
            .values(auto_reorder_eligible=True)
        )
        await session.commit()


# ==============================================================================
# 1. FINDING 1 TESTS: MODE A APPROVER AUTHORITY & SCOPE ENFORCEMENT
# ==============================================================================

@pytest.mark.asyncio
async def test_mode_a_approval_authorized_guardian_success():
    """Verify primary financial guardian (Alex Rivera) can approve staged Mode A proposal within ceiling."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Trigger Mode A reorder -> stages proposal in AWAITING_APPROVAL
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        assert reorder_res.status_code == 200
        prop = reorder_res.json()["proposal"]
        assert prop["state"] == "AWAITING_APPROVAL"

        # 2. Approve as Alex Rivera (authorized guardian)
        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-alex-01",
            "approval_type": "GUARDIAN_DELEGATED",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 200
        app_data = app_res.json()
        assert app_data["approver_user_id"] == "usr-alex-01"
        assert app_data["fingerprint_hash"] == prop["fingerprint_sha256"]

        # 3. Verify proposal transitioned to APPROVED
        async with AsyncSessionLocal() as session:
            p_db = await session.get(PurchaseProposal, prop["id"])
            assert p_db.state == TransactionLifecycleState.APPROVED


@pytest.mark.asyncio
async def test_mode_a_approval_authorized_recipient_success():
    """Verify care recipient (Eleanor Vance) can approve own proposal directly."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-eleanor-02",
        })
        assert reorder_res.status_code == 200
        prop = reorder_res.json()["proposal"]

        # Approve as recipient
        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-eleanor-02",
            "approval_type": "USER_DIRECT",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 200
        assert app_res.json()["approver_user_id"] == "usr-eleanor-02"


@pytest.mark.asyncio
async def test_mode_a_approval_unrelated_stranger_rejected():
    """Verify an arbitrary unrelated user cannot approve Eleanor's proposal."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-eleanor-02",
        })
        prop = reorder_res.json()["proposal"]

        # Create stranger user if not existing
        async with AsyncSessionLocal() as session:
            stranger = await session.get(User, "usr-stranger-99")
            if not stranger:
                session.add(User(
                    id="usr-stranger-99",
                    email="stranger@attacker.demo",
                    full_name="Unauthorized Stranger",
                    is_active=True,
                ))
                await session.commit()

        # Attempt approval as stranger
        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-stranger-99",
            "approval_type": "USER_DIRECT",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 403
        assert "no registered guardian relationship" in app_res.json()["detail"].lower()

        # Invariant: No approval record created for stranger
        async with AsyncSessionLocal() as session:
            approvals = (await session.execute(
                select(Approval).where(
                    Approval.fingerprint_hash == prop["fingerprint_sha256"],
                    Approval.approver_user_id == "usr-stranger-99"
                )
            )).scalars().all()
            assert len(approvals) == 0


@pytest.mark.asyncio
async def test_mode_a_approval_guardian_without_authority_rejected():
    """Verify guardian with can_approve=False is rejected with HTTP 403 upon approval attempt."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create proposal by recipient so it enters AWAITING_APPROVAL cleanly
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-eleanor-02",
        })
        prop = reorder_res.json()["proposal"]

        # Revoke guardian's approval authority
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(GuardianRelationship)
                .where(GuardianRelationship.guardian_user_id == "usr-alex-01")
                .values(can_approve=False)
            )
            await session.commit()

        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-alex-01",
            "approval_type": "GUARDIAN_DELEGATED",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 403
        assert "does not possess delegated approval authority" in app_res.json()["detail"]


@pytest.mark.asyncio
async def test_mode_a_approval_guardian_ceiling_exceeded_rejected():
    """Verify guardian whose ceiling is lower than proposal total is rejected with HTTP 403 upon approval."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-eleanor-02",
        })
        prop = reorder_res.json()["proposal"]

        # Set Alex's ceiling to $20.00 ($2,000 cents), below CGM cost of $32.50 ($3,250 cents)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(GuardianRelationship)
                .where(GuardianRelationship.guardian_user_id == "usr-alex-01")
                .values(max_approval_amount_cents=2000)
            )
            await session.commit()

        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-alex-01",
            "approval_type": "GUARDIAN_DELEGATED",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 403
        assert "exceeds guardian's delegated limit" in app_res.json()["detail"]


@pytest.mark.asyncio
async def test_mode_a_approval_revoked_guardian_rejected():
    """Verify guardian relationship marked is_active=False is rejected with HTTP 403 upon approval."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-eleanor-02",
        })
        prop = reorder_res.json()["proposal"]

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(GuardianRelationship)
                .where(GuardianRelationship.guardian_user_id == "usr-alex-01")
                .values(is_active=False)
            )
            await session.commit()

        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-alex-01",
            "approval_type": "GUARDIAN_DELEGATED",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 403
        assert "inactive or revoked" in app_res.json()["detail"]


@pytest.mark.asyncio
async def test_mode_a_approval_deny_proposal_rejected():
    """Verify proposal with DENY policy decision cannot be approved."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create proposal in AWAITING_APPROVAL via Mode A
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        prop = reorder_res.json()["proposal"]

        # Record a DENY policy evaluation on this proposal
        async with AsyncSessionLocal() as session:
            session.add(PolicyEvaluationRecord(
                id=f"eval-deny-{uuid.uuid4().hex[:8]}",
                proposal_id=prop["id"],
                decision=PolicyDecision.DENY,
                rules_evaluated=["RULE_MONTHLY_BUDGET_CEILING"],
                passed_checks=[],
                failed_checks=["EXCEEDS_MONTHLY_BUDGET"],
                human_explanation="Over budget policy denial",
                fingerprint_hash=prop["fingerprint_sha256"],
                policy_version=1,
                created_at=datetime.now(timezone.utc),
            ))
            await session.commit()

        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": prop["id"],
            "fingerprint_hash": prop["fingerprint_sha256"],
            "approver_user_id": "usr-alex-01",
            "approval_type": "GUARDIAN_DELEGATED",
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 403
        assert "deny" in app_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generic_care_evaluation_with_schedule_and_baseline_success():
    """Verify generic POST /api/trust/evaluate enforces schedule and baseline prices for CARE."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a CARE proposal via reorder endpoint
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        prop = reorder_res.json()["proposal"]

        # Switch to Mode B to test autonomous schedule requirement
        await ac.post("/api/care/mode", json={"care_profile_id": "care-eleanor-01", "mode": "MODE_B_CONTROLLED_AUTO"})

        # Call generic evaluate endpoint on this proposal
        eval_res = await ac.post("/api/trust/evaluate", json={
            "proposal_id": prop["id"],
            "acting_user_id": "usr-alex-01",
        })
        assert eval_res.status_code == 200
        data = eval_res.json()
        assert data["decision"] == PolicyDecision.ALLOW
        assert "RULE_REORDER_SCHEDULE_ACTIVE" in data["rules_evaluated"]
        assert "RULE_PRICE_TOLERANCE" in data["rules_evaluated"]


@pytest.mark.asyncio
async def test_generic_care_evaluation_medical_safety_restriction_denial():
    """Verify generic evaluate denies an item with auto_reorder_eligible=False."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a reorder with an ineligible item via database and test generic evaluate
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        prop = reorder_res.json()["proposal"]

        # Mark the schedule as auto_reorder_eligible=False
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ReorderSchedule)
                .where(ReorderSchedule.id == "sched-cgm-01")
                .values(auto_reorder_eligible=False)
            )
            await session.commit()

        eval_res = await ac.post("/api/trust/evaluate", json={
            "proposal_id": prop["id"],
            "acting_user_id": "usr-alex-01",
        })
        assert eval_res.status_code == 200
        data = eval_res.json()
        assert data["decision"] == PolicyDecision.DENY
        assert "MEDICAL SAFETY RESTRICTION" in data["human_explanation"]

        # Restore schedule
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ReorderSchedule)
                .where(ReorderSchedule.id == "sched-cgm-01")
                .values(auto_reorder_eligible=True)
            )
            await session.commit()


# ==============================================================================
# 2. FINDING 2 TESTS: SIMULATED FINANCIAL ACCOUNTING ISOLATION
# ==============================================================================

@pytest.mark.asyncio
async def test_simulated_capture_does_not_mutate_genuine_monthly_spent():
    """Verify SIMULATED checkout does not increment genuine monthly_spent_cents."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prof_before = (await ac.get("/api/care/profile?care_profile_id=care-eleanor-01")).json()
        initial_genuine_spent = prof_before["monthly_spent"]

        await ac.post("/api/care/mode", json={"care_profile_id": "care-eleanor-01", "mode": "MODE_B_CONTROLLED_AUTO"})
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        reorder_data = reorder_res.json()
        prop_id = reorder_data["proposal"]["id"]
        order_id = reorder_data["order"]["order_id"]
        total_cents = reorder_data["proposal"]["total_cents"]

        # Capture simulated payment
        cap_res = await ac.post(f"/api/payments/{prop_id}/capture", json={
            "proposal_id": prop_id,
            "paypal_order_id": order_id,
        })
        assert cap_res.status_code == 200
        assert cap_res.json()["status"] == "SIMULATED"
        assert cap_res.json()["is_simulated"] is True

        # Invariant 1: Genuine monthly spend is NOT altered
        prof_after = (await ac.get("/api/care/profile?care_profile_id=care-eleanor-01")).json()
        assert prof_after["monthly_spent"] == initial_genuine_spent

        # Invariant 2: ConsentPolicy genuine spend is NOT altered
        async with AsyncSessionLocal() as session:
            pol = (await session.execute(
                select(ConsentPolicy).where(ConsentPolicy.id == "pol-002")
            )).scalar_one()
            assert pol.monthly_spent_cents == int(round(initial_genuine_spent * 100))

            # Invariant 3: Simulated demo metric IS recorded in isolated field
            cp = await session.get(CareProfile, "care-eleanor-01")
            assert cp.simulated_monthly_spent_cents >= total_cents
            assert cp.reserved_cents == 0

            # Invariant 4: Transaction state remains PAYMENT_PENDING, NEVER genuine PAID
            tx = (await session.execute(
                select(Transaction).where(Transaction.proposal_id == prop_id)
            )).scalar_one()
            assert tx.paypal_status == "SIMULATED"
            assert tx.state == TransactionLifecycleState.PAYMENT_PENDING


# ==============================================================================
# 3. FINDING 3 TESTS: ATOMIC CARE BUDGET RESERVATIONS & CONCURRENCY
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_reorders_budget_exhaustion_fails_closed():
    """
    Concurrency test:
    - Budget: $250.00
    - Existing spent: $200.00 ($20,000 cents) -> Available: $50.00
    - Two concurrent reorder requests for $32.50 each ($65.00 total > $50.00)
    - Exactly one must succeed, the other must fail closed.
    - Combined spending + reservations must NEVER exceed $250.00.
    """
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(CareProfile)
            .where(CareProfile.id == "care-eleanor-01")
            .values(monthly_spent_cents=20000, reserved_cents=0)
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/care/mode", json={"care_profile_id": "care-eleanor-01", "mode": "MODE_B_CONTROLLED_AUTO"})

        req1 = ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        req2 = ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })

        res1, res2 = await asyncio.gather(req1, req2)
        statuses = [res1.status_code, res2.status_code]
        decisions = [
            res1.json().get("decision"),
            res2.json().get("decision")
        ]

        assert 200 in statuses
        assert PolicyDecision.ALLOW in decisions
        assert PolicyDecision.DENY in decisions

        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-eleanor-01")
            total_committed = cp.monthly_spent_cents + cp.reserved_cents
            assert total_committed <= 25000
            assert cp.reserved_cents == 3250


@pytest.mark.asyncio
async def test_duplicate_proposal_reservation_rejected():
    """Verify the same proposal cannot be reserved twice."""
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        proposal = PurchaseProposal(
            id=f"prop-resv-{uuid.uuid4().hex[:8]}",
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Duplicate Test",
            merchant_id="merch-pharmacy-01",
            merchant_name="Pharmacy",
            subtotal_cents=1000,
            total_cents=1000,
            currency="USD",
            ai_reasoning="Test",
            state=TransactionLifecycleState.POLICY_CHECK,
        )
        session.add(proposal)
        await session.commit()

        ok1, resv1, _ = await reserve_care_budget(session, proposal, cp)
        assert ok1 is True
        assert resv1.status == ReservationStatus.HELD

        ok2, resv2, _ = await reserve_care_budget(session, proposal, cp)
        assert ok2 is True
        assert resv2.id == resv1.id


@pytest.mark.asyncio
async def test_consume_care_budget_idempotent():
    """Verify consume_care_budget only increments spending once, preventing double counting."""
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        initial_spent = cp.monthly_spent_cents

        proposal = PurchaseProposal(
            id=f"prop-consume-{uuid.uuid4().hex[:8]}",
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Consume Test",
            merchant_id="merch-pharmacy-01",
            merchant_name="Pharmacy",
            subtotal_cents=2500,
            total_cents=2500,
            currency="USD",
            ai_reasoning="Test",
            state=TransactionLifecycleState.APPROVED,
        )
        session.add(proposal)
        await session.flush()

        ok, resv, _ = await reserve_care_budget(session, proposal, cp)
        assert ok is True
        await session.commit()

        consumed1 = await consume_care_budget(session, proposal.id)
        assert consumed1 is True
        await session.commit()

        consumed2 = await consume_care_budget(session, proposal.id)
        assert consumed2 is False
        await session.commit()

        await session.refresh(cp)
        assert cp.monthly_spent_cents == initial_spent + 2500
        assert cp.reserved_cents == 0


# ==============================================================================
# 4. DEFECT 1: MODE B AUTOMATIC APPROVAL VS GUARDIAN APPROVAL SEPARATION
# ==============================================================================

@pytest.mark.asyncio
async def test_caller_cannot_manufacture_policy_automatic_via_approve_endpoint():
    """Defect 1: External callers cannot manufacture POLICY_AUTOMATIC approvals via /api/trust/approve."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a proposal in AWAITING_APPROVAL
        prop_res = await ac.post("/api/trust/proposals", json={
            "user_id": "usr-eleanor-02",
            "care_profile_id": "care-eleanor-01",
            "module": "CARE",
            "title": "Unauthorized Automatic Approval Test",
            "merchant_id": "m-dexcom-01",
            "merchant_name": "Dexcom Official",
            "items": [{
                "product_id": "prod-cgm-01",
                "name": "Dexcom G7 Sensor",
                "category": "Medical Supplies",
                "quantity": 1,
                "unit_price_cents": 3250,
            }],
            "subtotal_cents": 3250,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Test",
        })
        assert prop_res.status_code == 200
        prop = prop_res.json()
        p_id = prop["id"]
        fp = prop["fingerprint_sha256"]

        await ac.post(f"/api/trust/transactions/{p_id}/transition", json={
            "target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"
        })
        await ac.post(f"/api/trust/transactions/{p_id}/transition", json={
            "target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"
        })

        # Attacker tries to submit POLICY_AUTOMATIC
        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": p_id,
            "approver_user_id": "usr-alex-01",
            "approval_type": "POLICY_AUTOMATIC",
            "fingerprint_hash": fp,
            "ttl_seconds": 86400,
        })
        assert app_res.status_code == 403
        assert "Automatic policy approval cannot be granted through the public human approval endpoint" in app_res.json()["detail"]


@pytest.mark.asyncio
async def test_mode_b_deny_cannot_produce_automatic_approval():
    """Defect 1: Mode B reorder with medical safety restriction produces DENY, never automatic approval."""
    async with AsyncSessionLocal() as session:
        # Make CGM sensor ineligible for autonomous reorder
        await session.execute(
            update(ReorderSchedule)
            .where(ReorderSchedule.id == "sched-cgm-01")
            .values(auto_reorder_eligible=False)
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/care/mode", json={"care_profile_id": "care-eleanor-01", "mode": "MODE_B_CONTROLLED_AUTO"})
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        # Fails closed on safety rule
        assert reorder_res.status_code == 400
        assert "Safety rule failure" in reorder_res.json()["detail"]


# ==============================================================================
# 5. DEFECT 2: BUDGET RESERVATION COMPLETE RELEASE LIFECYCLE
# ==============================================================================

@pytest.mark.asyncio
async def test_transition_to_rejected_releases_budget_reservation():
    """Defect 2: Transitioning a proposal to REJECTED safely releases the held budget reservation."""
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        proposal = PurchaseProposal(
            id=f"prop-reject-{uuid.uuid4().hex[:8]}",
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Reject Release Test",
            merchant_id="merch-01",
            merchant_name="Merchant",
            subtotal_cents=4500,
            total_cents=4500,
            currency="USD",
            ai_reasoning="Test",
            state=TransactionLifecycleState.POLICY_CHECK,
        )
        session.add(proposal)
        await session.flush()

        ok, resv, _ = await reserve_care_budget(session, proposal, cp)
        assert ok is True
        await session.commit()

        await session.refresh(cp)
        assert cp.reserved_cents == 4500

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        trans_res = await ac.post(f"/api/trust/transactions/{proposal.id}/transition", json={
            "target_state": "REJECTED",
            "actor_id": "usr-alex-01",
            "actor_role": "SYSTEM",
            "reason": "Policy rejection test",
        })
        assert trans_res.status_code == 200

        async with AsyncSessionLocal() as session:
            cp_after = await session.get(CareProfile, "care-eleanor-01")
            assert cp_after.reserved_cents == 0
            r = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == proposal.id))
            assert r.status == ReservationStatus.RELEASED


@pytest.mark.asyncio
async def test_payment_cancellation_releases_budget_reservation():
    """Defect 2: Cancelling a payment attempt safely releases the held budget reservation."""
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        proposal = PurchaseProposal(
            id=f"prop-cancel-{uuid.uuid4().hex[:8]}",
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Cancel Release Test",
            merchant_id="merch-01",
            merchant_name="Merchant",
            subtotal_cents=3000,
            total_cents=3000,
            currency="USD",
            ai_reasoning="Test",
            state=TransactionLifecycleState.APPROVED,
        )
        session.add(proposal)
        await session.flush()

        ok, resv, _ = await reserve_care_budget(session, proposal, cp)
        assert ok is True
        await session.commit()

        await session.refresh(cp)
        assert cp.reserved_cents == 3000

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        cancel_res = await ac.post(f"/api/payments/{proposal.id}/cancel")
        assert cancel_res.status_code == 200

        async with AsyncSessionLocal() as session:
            cp_after = await session.get(CareProfile, "care-eleanor-01")
            assert cp_after.reserved_cents == 0
            r = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == proposal.id))
            assert r.status == ReservationStatus.RELEASED


@pytest.mark.asyncio
async def test_deterministic_expiry_processor_releases_expired_reservation():
    """Defect 2: Deterministic expiry processor detects expired approvals and releases reservations."""
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        now_utc = datetime.now(timezone.utc)
        proposal = PurchaseProposal(
            id=f"prop-expired-{uuid.uuid4().hex[:8]}",
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Expired Approval Test",
            merchant_id="merch-01",
            merchant_name="Merchant",
            subtotal_cents=5000,
            total_cents=5000,
            currency="USD",
            ai_reasoning="Test",
            state=TransactionLifecycleState.APPROVED,
        )
        session.add(proposal)
        await session.flush()

        # Add fingerprint and expired approval (expired 1 hour ago)
        fake_fp = f"fp-{uuid.uuid4().hex}"
        fp_rec = TransactionFingerprint(
            proposal_id=proposal.id,
            canonical_payload="{}",
            sha256_hash=fake_fp,
            policy_version=1,
            created_at=now_utc - timedelta(hours=2),
        )
        session.add(fp_rec)

        appr = Approval(
            fingerprint_hash=fake_fp,
            approver_user_id="usr-alex-01",
            approval_type=ApprovalType.USER_DIRECT,
            expires_at=now_utc - timedelta(hours=1),
            is_revoked=False,
        )
        session.add(appr)

        ok, resv, _ = await reserve_care_budget(session, proposal, cp)
        assert ok is True
        await session.commit()

        await session.refresh(cp)
        assert cp.reserved_cents == 5000

        # Invoke deterministic expiry processor
        released_count = await process_expired_reservations_and_proposals(session, care_profile_id=cp.id)
        assert released_count >= 1

        await session.refresh(cp)
        assert cp.reserved_cents == 0

        r = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == proposal.id))
        assert r.status == ReservationStatus.RELEASED


@pytest.mark.asyncio
async def test_concurrent_reservation_release_separate_sessions():
    """Defect 2 Concurrency: Two separate AsyncSessions attempting to release the same reservation; exactly one succeeds."""
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        proposal = PurchaseProposal(
            id=f"prop-conc-rel-{uuid.uuid4().hex[:8]}",
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Concurrent Release Test",
            merchant_id="merch-01",
            merchant_name="Merchant",
            subtotal_cents=3500,
            total_cents=3500,
            currency="USD",
            ai_reasoning="Test",
            state=TransactionLifecycleState.POLICY_CHECK,
        )
        session.add(proposal)
        await session.flush()

        ok, resv, _ = await reserve_care_budget(session, proposal, cp)
        assert ok is True
        await session.commit()

    # Open two separate sessions and attempt concurrent release
    async def do_release():
        async with AsyncSessionLocal() as s:
            res = await release_care_budget(s, proposal.id, is_simulated=False)
            await s.commit()
            return res

    results = await asyncio.gather(do_release(), do_release())
    assert True in results
    assert False in results # Exactly one succeeded, second was idempotent no-op

    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        assert cp.reserved_cents == 0


# ==============================================================================
# 6. DEFECT 3: PRE-NETWORK DATABASE TRANSACTION COMMIT & IDEMPOTENCY
# ==============================================================================

@pytest.mark.asyncio
async def test_order_creation_commits_db_before_paypal_network_call():
    """Defect 3: Verifies that the database transaction is committed before PayPal network call runs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create approved proposal
        prop_res = await ac.post("/api/trust/proposals", json={
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "DB Transaction Commit Test",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [{
                "product_id": "prod-clip-01",
                "name": "Cable Clips",
                "category": "Peripherals",
                "quantity": 1,
                "unit_price_cents": 1200,
            }],
            "subtotal_cents": 1200,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Test",
        })
        prop = prop_res.json()
        p_id = prop["id"]
        fp = prop["fingerprint_sha256"]

        await ac.post(f"/api/trust/transactions/{p_id}/transition", json={
            "target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"
        })
        await ac.post(f"/api/trust/transactions/{p_id}/transition", json={
            "target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"
        })
        await ac.post("/api/trust/approve", json={
            "proposal_id": p_id,
            "approver_user_id": "usr-alex-01",
            "approval_type": "USER_DIRECT",
            "fingerprint_hash": fp,
            "ttl_seconds": 3600,
        })

        # Intercept client.create_order to inspect DB state from a separate session while network call is in flight
        verified_pre_committed = False

        original_create_order = PayPalService().client.create_order

        async def inspect_db_during_call(*args, **kwargs):
            nonlocal verified_pre_committed
            # Separate session inspecting the committed database state
            async with AsyncSessionLocal() as check_session:
                tx = await check_session.scalar(
                    select(Transaction).where(Transaction.proposal_id == p_id)
                )
                if tx and tx.reconciliation_status == "ORDER_CREATION_IN_PROGRESS" and tx.state == TransactionLifecycleState.PAYMENT_PENDING:
                    verified_pre_committed = True
            return await original_create_order(*args, **kwargs)

        with patch("app.payments.paypal_service.PayPalClient.create_order", side_effect=inspect_db_during_call):
            ord_res = await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})
            assert ord_res.status_code == 200

        assert verified_pre_committed is True


@pytest.mark.asyncio
async def test_order_creation_definite_failure_releases_reservation():
    """Defect 3: Definite provider rejection on order creation releases the held reservation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-eleanor-02",
            "module": "CARE",
            "care_profile_id": "care-eleanor-01",
            "title": "Definite Failure Test",
            "merchant_id": "m-cvs-01",
            "merchant_name": "CVS Pharmacy",
            "items": [
                {
                    "product_id": "prod-med-01",
                    "name": "Blood Pressure Monitor",
                    "category": "Medical Supplies",
                    "quantity": 1,
                    "unit_price_cents": 2000,
                }
            ],
            "subtotal_cents": 2000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Test prescribed monitor.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        assert res_prop.status_code == 200
        prop_data = res_prop.json()
        proposal_id = prop_data["id"]
        fp = prop_data["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-eleanor-02", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-eleanor-02", "actor_role": "SYSTEM"},
        )
        appr_res = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-sarah-02",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fp,
                "ttl_seconds": 3600,
            },
        )
        assert appr_res.status_code == 200

        # Reserve budget
        async with AsyncSessionLocal() as session:
            proposal = await session.get(PurchaseProposal, proposal_id)
            cp = await session.get(CareProfile, "care-eleanor-01")
            initial_reserved = cp.reserved_cents
            ok, resv, _ = await reserve_care_budget(session, proposal, cp)
            assert ok is True
            await session.commit()

            await session.refresh(cp)
            assert cp.reserved_cents == initial_reserved + 2000

        # Mock definite 400 rejection from PayPal
        with patch("app.payments.paypal_service.PayPalClient.create_order", side_effect=PaymentVerificationError("PayPal order creation rejected: 400 Bad Request")):
            ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
            assert ord_res.status_code == 400

        async with AsyncSessionLocal() as session:
            cp_after = await session.get(CareProfile, "care-eleanor-01")
            assert cp_after.reserved_cents == initial_reserved
            r = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id))
            assert r.status == ReservationStatus.RELEASED


@pytest.mark.asyncio
async def test_order_creation_timeout_preserves_reservation_and_marks_reconciliation_required():
    """Defect 3: Ambiguous provider timeout retains the held reservation and sets RECONCILIATION_REQUIRED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-eleanor-02",
            "module": "CARE",
            "care_profile_id": "care-eleanor-01",
            "title": "Timeout Test",
            "merchant_id": "m-cvs-01",
            "merchant_name": "CVS Pharmacy",
            "items": [
                {
                    "product_id": "prod-med-02",
                    "name": "Walker Glides",
                    "category": "Medical Supplies",
                    "quantity": 1,
                    "unit_price_cents": 2200,
                }
            ],
            "subtotal_cents": 2200,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Replacement glides.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        assert res_prop.status_code == 200
        prop_data = res_prop.json()
        proposal_id = prop_data["id"]
        fp = prop_data["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-eleanor-02", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-eleanor-02", "actor_role": "SYSTEM"},
        )
        appr_res = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-sarah-02",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fp,
                "ttl_seconds": 3600,
            },
        )
        assert appr_res.status_code == 200

        # Reserve budget
        async with AsyncSessionLocal() as session:
            proposal = await session.get(PurchaseProposal, proposal_id)
            cp = await session.get(CareProfile, "care-eleanor-01")
            initial_reserved = cp.reserved_cents
            ok, resv, _ = await reserve_care_budget(session, proposal, cp)
            assert ok is True
            await session.commit()

            await session.refresh(cp)
            assert cp.reserved_cents == initial_reserved + 2200

        # Mock timeout from PayPal
        with patch("app.payments.paypal_service.PayPalClient.create_order", side_effect=PaymentCaptureError("PayPal network error creating order: timeout")):
            ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
            assert ord_res.status_code == 400

        async with AsyncSessionLocal() as session:
            cp_after = await session.get(CareProfile, "care-eleanor-01")
            assert cp_after.reserved_cents == initial_reserved + 2200  # Reservation strictly preserved!
            r = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id))
            assert r.status == ReservationStatus.HELD  # Still HELD!
            tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == proposal_id))
            assert tx.reconciliation_status == "RECONCILIATION_REQUIRED"
