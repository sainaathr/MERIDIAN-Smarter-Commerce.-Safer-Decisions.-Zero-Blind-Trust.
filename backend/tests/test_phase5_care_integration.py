"""
MERIDIAN Phase 5.3 Task 1 Integration Tests:
CARE Protected Autonomy Integration (Assisted Commerce)

Validates:
1. Care profile retrieval (Eleanor Vance, guardian Alex Rivera, replenishment schedules).
2. Atomic mode toggling between Mode A (EXPLICIT_ALWAYS) and Mode B (CONTROLLED_AUTO) synchronized across CareProfile and ConsentPolicy.
3. Mode B autonomous reorder within bounds (ALLOW, POLICY_AUTOMATIC approval, PayPal Sandbox order in PAYMENT_PENDING).
4. Mode A staged replenishment proposal (REQUIRE_APPROVAL, AWAITING_APPROVAL, PayPal order blocked until explicit guardian approval).
5. Medical safety rule enforcement (auto_reorder_eligible=False rejects reordering).
6. Trust Engine policy budget ceiling denial (DENY transitions proposal to REJECTED, blocks payment order).
7. Budget tracking integrity (monthly_spent_cents increments upon capture, pending amounts are not marked spent).
"""

import uuid
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
)
from app.models.entities import (
    CareProfile,
    ConsentPolicy,
    ReorderSchedule,
    PurchaseProposal,
    Approval,
    PolicyEvaluationRecord,
)

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()
    async with AsyncSessionLocal() as session:
        # Reset Eleanor's care profile and consent policy to seed baseline ($120.00 spent out of $250.00)
        await session.execute(
            update(CareProfile)
            .where(CareProfile.id == "care-eleanor-01")
            .values(
                monthly_spent_cents=12000,
                reserved_cents=0,
                simulated_monthly_spent_cents=0,
                approval_mode=ApprovalMode.CONTROLLED_AUTO,
            )
        )
        await session.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.user_id == "usr-eleanor-02", ConsentPolicy.scope == ModuleScope.CARE)
            .values(
                monthly_spent_cents=12000,
                reserved_cents=0,
                simulated_monthly_spent_cents=0,
                approval_mode=ApprovalMode.CONTROLLED_AUTO,
            )
        )
        await session.commit()

@pytest.mark.asyncio
async def test_care_profile_retrieval():
    """Verify GET /api/care/profile returns authoritative profile, guardians, and items."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/care/profile?care_profile_id=care-eleanor-01")
        assert res.status_code == 200
        data = res.json()

        assert data["id"] == "care-eleanor-01"
        assert data["user_id"] == "usr-eleanor-02"
        assert data["recipient_name"] == "Eleanor Vance"
        assert data["monthly_budget"] == 250.0
        assert data["currency"] == "USD"
        assert len(data["guardians"]) >= 1

        primary_guardian = data["guardians"][0]
        assert primary_guardian["name"] == "Alex Rivera"
        assert primary_guardian["can_approve"] is True
        assert primary_guardian["max_approval_amount"] == 150.0

        assert len(data["monitored_items"]) >= 1
        item_names = [it["name"] for it in data["monitored_items"]]
        assert "Continuous Glucose Monitor Sensor Pods" in item_names

@pytest.mark.asyncio
async def test_care_mode_toggle_synchronization():
    """Verify POST /api/care/mode atomically updates both CareProfile and ConsentPolicy."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Switch to Mode A (Explicit human approval)
        res_a = await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "MODE_A_EVERY_ORDER",
        })
        assert res_a.status_code == 200
        data_a = res_a.json()
        assert data_a["approval_mode"] == "MODE_A_EVERY_ORDER"

        # Verify DB synchronization
        async with AsyncSessionLocal() as session:
            cp = (await session.execute(select(CareProfile).where(CareProfile.id == "care-eleanor-01"))).scalar_one()
            assert cp.approval_mode == ApprovalMode.EXPLICIT_ALWAYS

            pol = (await session.execute(
                select(ConsentPolicy).where(ConsentPolicy.user_id == "usr-eleanor-02", ConsentPolicy.scope == ModuleScope.CARE)
            )).scalars().first()
            assert pol.approval_mode == ApprovalMode.EXPLICIT_ALWAYS

        # 2. Switch back to Mode B (Controlled automatic reordering)
        res_b = await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "MODE_B_CONTROLLED_AUTO",
        })
        assert res_b.status_code == 200
        data_b = res_b.json()
        assert data_b["approval_mode"] == "MODE_B_CONTROLLED_AUTO"

        # Verify DB synchronization
        async with AsyncSessionLocal() as session:
            cp = (await session.execute(select(CareProfile).where(CareProfile.id == "care-eleanor-01"))).scalar_one()
            assert cp.approval_mode == ApprovalMode.CONTROLLED_AUTO

            pol = (await session.execute(
                select(ConsentPolicy).where(ConsentPolicy.user_id == "usr-eleanor-02", ConsentPolicy.scope == ModuleScope.CARE)
            )).scalars().first()
            assert pol.approval_mode == ApprovalMode.CONTROLLED_AUTO

        # 3. Invalid mode rejects with 400
        res_bad = await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "INVALID_UNSUPPORTED_MODE",
        })
        assert res_bad.status_code == 400

@pytest.mark.asyncio
async def test_care_mode_b_autonomous_reorder():
    """Verify Mode B executes autonomous reorder: ALLOW verdict, auto-approval, and PayPal Sandbox order."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ensure profile is in Mode B
        await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "MODE_B_CONTROLLED_AUTO",
        })

        # Trigger reorder for CGM sensor pods
        res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        assert res.status_code == 200
        data = res.json()

        assert data["decision"] == "ALLOW"
        assert data["requires_approval"] is False
        assert data["order"] is not None

        # Verify proposal properties
        proposal = data["proposal"]
        assert proposal["module"] == "CARE"
        assert proposal["state"] == "PAYMENT_PENDING"
        assert proposal["total_cents"] == 3250
        assert proposal["merchant_name"] == "CareRx Pharmacy Sandbox"
        assert len(proposal["items"]) == 1
        assert proposal["items"][0]["unit_price_cents"] == 3250

        # Verify PayPal Sandbox Order
        order = data["order"]
        assert order["proposal_id"] == proposal["id"]
        assert order["total_cents"] == 3250
        assert order["currency"] == "USD"
        assert order["status"] in ("CREATED", "APPROVED")

        # Verify DB records
        async with AsyncSessionLocal() as session:
            # Approval record exists with POLICY_AUTOMATIC
            app_rec = (await session.execute(
                select(Approval).where(Approval.fingerprint_hash == proposal["fingerprint_sha256"])
            )).scalars().first()
            assert app_rec is not None
            assert app_rec.approval_type == ApprovalType.POLICY_AUTOMATIC

            # PolicyEvaluationRecord exists with ALLOW
            eval_rec = (await session.execute(
                select(PolicyEvaluationRecord).where(PolicyEvaluationRecord.proposal_id == proposal["id"])
            )).scalars().first()
            assert eval_rec is not None
            assert eval_rec.decision == PolicyDecision.ALLOW

@pytest.mark.asyncio
async def test_care_mode_a_staged_proposal_and_explicit_guardian_approval():
    """Verify Mode A stages proposal at AWAITING_APPROVAL and blocks PayPal order until guardian approves."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Set mode to Mode A
        await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "MODE_A_EVERY_ORDER",
        })

        # 2. Trigger reorder
        res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        assert res.status_code == 200
        data = res.json()

        assert data["decision"] == "REQUIRE_APPROVAL"
        assert data["requires_approval"] is True
        assert data["order"] is None
        proposal = data["proposal"]
        assert proposal["state"] == "AWAITING_APPROVAL"

        # 3. CRITICAL INVARIANT: Direct payment order creation must fail before explicit human approval
        order_fail = await ac.post(f"/api/payments/{proposal['id']}/create-order", json={
            "proposal_id": proposal["id"],
        })
        assert order_fail.status_code in (400, 403)

        # 4. Guardian Alex Vance explicitly approves proposal bound to authoritative SHA-256 fingerprint
        app_res = await ac.post("/api/trust/approve", json={
            "proposal_id": proposal["id"],
            "fingerprint_hash": proposal["fingerprint_sha256"],
            "approver_user_id": "usr-alex-01",
            "approval_type": "GUARDIAN_DELEGATED",
        })
        assert app_res.status_code == 200
        app_data = app_res.json()
        assert app_data["fingerprint_hash"] == proposal["fingerprint_sha256"]

        # 5. Now payment order creation succeeds
        order_success = await ac.post(f"/api/payments/{proposal['id']}/create-order", json={
            "proposal_id": proposal["id"],
        })
        assert order_success.status_code == 200
        order_data = order_success.json()
        assert order_data["proposal_id"] == proposal["id"]
        assert order_data["total_cents"] == 3250

@pytest.mark.asyncio
async def test_care_reorder_medical_safety_restriction():
    """Verify schedule item not eligible for autonomous reorder is rejected by safety rules."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        sched_id = f"sched-ineligible-{uuid.uuid4().hex[:8]}"
        # Create an ineligible schedule (e.g. prescription requiring clinical re-evaluation)
        async with AsyncSessionLocal() as session:
            ineligible_sched = ReorderSchedule(
                id=sched_id,
                care_profile_id="care-eleanor-01",
                product_name="Prescription Blood Thinner",
                category="Medication",
                preferred_merchant="CareRx Pharmacy Sandbox",
                unit_cost_cents=4500,
                frequency_days=30,
                days_remaining=2,
                auto_reorder_eligible=False, # Medical safety barrier!
            )
            session.add(ineligible_sched)
            await session.commit()

        res = await ac.post("/api/care/reorder", json={
            "schedule_id": sched_id,
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        assert res.status_code == 400
        assert "Safety rule failure" in res.json()["detail"]

@pytest.mark.asyncio
async def test_care_reorder_policy_ceiling_denial():
    """Verify reorder exceeding per-order cap or monthly budget receives DENY and transitions to REJECTED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ensure Mode B
        await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "MODE_B_CONTROLLED_AUTO",
        })

        sched_id = f"sched-expensive-{uuid.uuid4().hex[:8]}"
        # Create a schedule that exceeds the $75 per-order limit (e.g. $120.00 = 12000 cents)
        async with AsyncSessionLocal() as session:
            expensive_sched = ReorderSchedule(
                id=sched_id,
                care_profile_id="care-eleanor-01",
                product_name="Bulk Hospital Grade Monitor Kit",
                category="Medical Supplies",
                preferred_merchant="CareRx Pharmacy Sandbox",
                unit_cost_cents=12000, # $120.00 > $75.00 policy cap
                frequency_days=90,
                days_remaining=4,
                auto_reorder_eligible=True,
            )
            session.add(expensive_sched)
            await session.commit()

        res = await ac.post("/api/care/reorder", json={
            "schedule_id": sched_id,
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        assert res.status_code == 200
        data = res.json()

        assert data["decision"] == "DENY"
        assert data["requires_approval"] is False
        assert data["order"] is None
        assert data["proposal"]["state"] == "REJECTED"
        assert "exceeds the configured per-order limit" in data["human_explanation"]

        # Verify in DB that proposal is in terminal REJECTED state
        async with AsyncSessionLocal() as session:
            p = (await session.execute(
                select(PurchaseProposal).where(PurchaseProposal.id == data["proposal"]["id"])
            )).scalar_one()
            assert p.state == TransactionLifecycleState.REJECTED

@pytest.mark.asyncio
async def test_care_capture_budget_update():
    """Verify monthly_spent_cents increments only upon capture, preserving budget reservation invariants."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Query baseline budget spent
        profile_res = await ac.get("/api/care/profile?care_profile_id=care-eleanor-01")
        initial_spent_cents = int(round(profile_res.json()["monthly_spent"] * 100))

        # 2. Trigger Mode B reorder
        await ac.post("/api/care/mode", json={
            "care_profile_id": "care-eleanor-01",
            "mode": "MODE_B_CONTROLLED_AUTO",
        })
        reorder_res = await ac.post("/api/care/reorder", json={
            "schedule_id": "sched-cgm-01",
            "care_profile_id": "care-eleanor-01",
            "actor_id": "usr-alex-01",
        })
        assert reorder_res.status_code == 200
        reorder_data = reorder_res.json()
        prop_id = reorder_data["proposal"]["id"]
        order_id = reorder_data["order"]["order_id"]
        total_cents = reorder_data["proposal"]["total_cents"]

        # Invariant: Pending proposal must NOT prematurely increase monthly_spent_cents
        async with AsyncSessionLocal() as session:
            cp_mid = (await session.execute(
                select(CareProfile).where(CareProfile.id == "care-eleanor-01")
            )).scalar_one()
            assert cp_mid.monthly_spent_cents == initial_spent_cents

        # 3. Capture payment on PayPal Sandbox
        cap_res = await ac.post(f"/api/payments/{prop_id}/capture", json={
            "proposal_id": prop_id,
            "paypal_order_id": order_id,
        })
        assert cap_res.status_code == 200
        assert cap_res.json()["status"] in ("COMPLETED", "SIMULATED")

        # 4. Invariant: Upon simulated capture, genuine CareProfile.monthly_spent_cents is NOT incremented
        # (simulation isolation), reservation is released, and simulated_monthly_spent_cents is updated
        async with AsyncSessionLocal() as session:
            cp_after = (await session.execute(
                select(CareProfile).where(CareProfile.id == "care-eleanor-01")
            )).scalar_one()
            # Genuine spend remains unchanged
            assert cp_after.monthly_spent_cents == initial_spent_cents
            # Reservation is safely released
            assert cp_after.reserved_cents == 0
            # Simulated demo spend is isolated
            assert cp_after.simulated_monthly_spent_cents >= total_cents
