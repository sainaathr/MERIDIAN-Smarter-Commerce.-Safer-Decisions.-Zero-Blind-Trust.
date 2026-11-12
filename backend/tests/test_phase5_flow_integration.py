"""
MERIDIAN Phase 5.3 Task 2: FLOW Backend Integration & Invariant Tests
Validates:
1. Business profile retrieval & financial metrics calculation
2. Invoice listing, filtering, and creation with strict validation
3. Deterministic 30-day daily cashflow forecast arithmetic
4. Exclusion of PAID and CANCELLED invoices from forward projections
5. Gate A — Safety reserve preservation ($8,000.00 floor)
6. Gate A — Safety reserve breach strict rejection (fails closed with HTTP 400)
7. Gate B — Universal Trust Engine policy evaluation against pol-003
8. Gate B — Merchant whitelist enforcement (AWS Cloud Sandbox, GitHub Enterprise, Figma Inc)
9. Gate B — Per-order limit enforcement ($300.00 ceiling)
10. Gate B — Explicit human approval required (Mode A)
11. End-to-end human approval and fingerprint verification
12. Tampering detection on business proposal resets/invalidates approval
13. Strict isolation from CARE reservations (zero BudgetReservation created)
14. Idempotent database seeding
"""

from datetime import datetime, timezone, timedelta
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, desc

from app.main import app
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_database
from app.models.entities import (
    BusinessProfile,
    Invoice,
    PurchaseProposal,
    Transaction,
    TransactionFingerprint,
    PolicyEvaluationRecord,
    Approval,
    AuditEvent,
    BudgetReservation,
    ConsentPolicy,
)
from app.models.enums import (
    ModuleScope,
    TransactionLifecycleState,
    PolicyDecision,
    ApprovalMode,
    AuditEventType,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()
    async with AsyncSessionLocal() as session:
        from sqlalchemy import delete
        seed_inv_ids = ["inv-rec-01", "inv-rec-02", "inv-pay-01", "inv-pay-02"]
        await session.execute(
            delete(Invoice).where(Invoice.id.notin_(seed_inv_ids))
        )
        await session.commit()


# ==============================================================================
# 1. Profile Retrieval & Seed Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_profile_retrieval_and_metrics():
    """Verify GET /api/business/profile returns authoritative database profile and metrics."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/business/profile")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "biz-acme-01"
        assert data["company_name"] == "Acme Design Studio"
        assert data["operating_cash_cents"] == 1420000
        assert data["operating_cash"] == 14200.0
        assert data["minimum_safety_reserve_cents"] == 800000
        assert data["minimum_safety_reserve"] == 8000.0
        assert data["currency"] == "USD"
        assert data["estimated_runway_days"] > 0
        assert data["total_unpaid_receivables_cents"] >= 555000 # 345000 + 210000
        assert data["total_unpaid_payables_cents"] >= 228000 # 48000 + 180000


@pytest.mark.asyncio
async def test_flow_idempotent_seeding():
    """Verify repeated calls to init_database never duplicate business profile or invoices."""
    await init_database()
    await init_database()
    async with AsyncSessionLocal() as session:
        bps = (await session.execute(select(BusinessProfile).where(BusinessProfile.id == "biz-acme-01"))).scalars().all()
        assert len(bps) == 1

        inv1 = (await session.execute(select(Invoice).where(Invoice.id == "inv-rec-01"))).scalars().all()
        assert len(inv1) == 1

        inv2 = (await session.execute(select(Invoice).where(Invoice.id == "inv-pay-01"))).scalars().all()
        assert len(inv2) == 1


# ==============================================================================
# 2. Invoices Query & Creation Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_invoices_retrieval_and_filtering():
    """Verify GET /api/business/invoices supports filtering by invoice_type and status."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # All invoices
        res = await ac.get("/api/business/invoices")
        assert res.status_code == 200
        all_invs = res.json()
        assert len(all_invs) >= 4

        # Filter RECEIVABLE
        res_rec = await ac.get("/api/business/invoices?invoice_type=RECEIVABLE")
        assert res_rec.status_code == 200
        recs = res_rec.json()
        assert len(recs) >= 2
        for r in recs:
            assert r["invoice_type"] == "RECEIVABLE"

        # Filter PAYABLE
        res_pay = await ac.get("/api/business/invoices?invoice_type=PAYABLE")
        assert res_pay.status_code == 200
        pays = res_pay.json()
        assert len(pays) >= 2
        for p in pays:
            assert p["invoice_type"] == "PAYABLE"


@pytest.mark.asyncio
async def test_flow_create_invoice_validation():
    """Verify POST /api/business/invoices validates invoice_type, amount, and dates."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        due_iso = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()

        # 1. Valid invoice creation
        valid_payload = {
            "counterparty": "Design Systems Inc",
            "invoice_type": "RECEIVABLE",
            "amount_cents": 125000,
            "due_date": due_iso,
            "category": "Consulting Retainer",
        }
        res = await ac.post("/api/business/invoices", json=valid_payload)
        assert res.status_code == 201
        data = res.json()
        assert data["counterparty"] == "Design Systems Inc"
        assert data["amount_cents"] == 125000
        assert data["amount"] == 1250.0
        assert data["invoice_type"] == "RECEIVABLE"
        assert data["status"] == "PENDING"

        # 2. Invalid invoice type rejection
        invalid_type = {**valid_payload, "invoice_type": "INVALID_TYPE"}
        res_bad_type = await ac.post("/api/business/invoices", json=invalid_type)
        assert res_bad_type.status_code == 422

        # 3. Non-positive amount rejection
        invalid_amount = {**valid_payload, "amount_cents": 0}
        res_bad_amt = await ac.post("/api/business/invoices", json=invalid_amount)
        assert res_bad_amt.status_code == 422


@pytest.mark.asyncio
async def test_flow_create_invoice_status_defaults_to_pending():
    """Verify newly created invoice defaults to PENDING status without client specification."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        due_iso = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
        payload = {
            "counterparty": "Acme Partner Beta",
            "invoice_type": "RECEIVABLE",
            "amount_cents": 80000,
            "due_date": due_iso,
            "category": "Consulting",
        }
        res = await ac.post("/api/business/invoices", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "PENDING"
        inv_id = data["id"]

        # Verify persisted record
        async with AsyncSessionLocal() as session:
            inv = await session.get(Invoice, inv_id)
            assert inv is not None
            assert inv.status == "PENDING"


@pytest.mark.asyncio
async def test_flow_create_invoice_rejects_non_pending_statuses():
    """
    Verify client cannot submit PAID, OVERDUE, or CANCELLED as initial status.
    Attempts must fail with HTTP 422 and no record must be persisted.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        due_iso = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        for forbidden_status in ("PAID", "OVERDUE", "CANCELLED", "paid", "Overdue"):
            payload = {
                "counterparty": f"Fraud Counterparty {forbidden_status}",
                "invoice_type": "PAYABLE",
                "amount_cents": 50000,
                "due_date": due_iso,
                "category": "Disallowed Category",
                "status": forbidden_status,
            }
            res = await ac.post("/api/business/invoices", json=payload)
            assert res.status_code == 422, f"Status '{forbidden_status}' should be rejected with 422"
            assert "Initial invoice status must be 'PENDING'" in str(res.json())

        # Verify no invoices with counterparty starting with "Fraud Counterparty" were persisted
        async with AsyncSessionLocal() as session:
            res_db = await session.execute(
                select(Invoice).where(Invoice.counterparty.like("Fraud Counterparty%"))
            )
            assert len(res_db.scalars().all()) == 0


@pytest.mark.asyncio
async def test_flow_create_invoice_explicit_pending_allowed():
    """Verify explicitly supplying status='PENDING' is accepted."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        due_iso = (datetime.now(timezone.utc) + timedelta(days=25)).isoformat()
        payload = {
            "counterparty": "Explicit Pending Vendor",
            "invoice_type": "PAYABLE",
            "amount_cents": 35000,
            "due_date": due_iso,
            "category": "Office",
            "status": "PENDING",
        }
        res = await ac.post("/api/business/invoices", json=payload)
        assert res.status_code == 201
        assert res.json()["status"] == "PENDING"


# ==============================================================================
# 3. Deterministic Cashflow Arithmetic & Paid Invoices Exclusion
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_cashflow_deterministic_arithmetic():
    """Verify GET /api/business/cashflow computes 30 daily cumulative balances in integer cents."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/business/cashflow?horizon_days=30")
        assert res.status_code == 200
        data = res.json()

        starting = data["starting_balance_cents"]
        reserve = data["minimum_safety_reserve_cents"]
        assert starting == 1420000
        assert reserve == 800000

        days = data["forecast_days"]
        assert len(days) == 30

        # Check cumulative balance consistency
        running = starting
        min_seen = starting
        for day in days:
            running += (day["receivables_cents"] - day["payables_cents"])
            assert day["projected_balance_cents"] == running
            if running < min_seen:
                min_seen = running

        assert data["projected_minimum_cash_cents"] == min_seen
        assert data["safety_reserve_margin_cents"] == min_seen - reserve


@pytest.mark.asyncio
async def test_flow_cashflow_excludes_paid_and_cancelled_invoices():
    """Verify that PAID and CANCELLED invoices are strictly excluded from future cashflow."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_before = await ac.get("/api/business/cashflow")
        assert res_before.status_code == 200
        before_rec = res_before.json()["total_receivables_cents"]

    async with AsyncSessionLocal() as session:
        now = datetime.now(timezone.utc)
        # Add a PAID invoice of $10,000 (1,000,000 cents)
        paid_inv = Invoice(
            id=f"inv-paid-{uuid.uuid4().hex[:6]}",
            business_profile_id="biz-acme-01",
            counterparty="Historic Customer",
            invoice_type="RECEIVABLE",
            amount_cents=1000000,
            due_date=now + timedelta(days=5),
            status="PAID",
            category="Settled",
        )
        # Add a CANCELLED payable invoice of $5,000 (500,000 cents)
        cancelled_inv = Invoice(
            id=f"inv-canc-{uuid.uuid4().hex[:6]}",
            business_profile_id="biz-acme-01",
            counterparty="Cancelled Supplier",
            invoice_type="PAYABLE",
            amount_cents=500000,
            due_date=now + timedelta(days=5),
            status="CANCELLED",
            category="Voided",
        )
        session.add_all([paid_inv, cancelled_inv])
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/business/cashflow")
        assert res.status_code == 200
        data = res.json()
        # Invariant: The 1,000,000 cents from the PAID invoice is strictly excluded
        assert data["total_receivables_cents"] == before_rec


# ==============================================================================
# 4. Gate A — Financial Affordability & Safety Reserve Enforcement
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_affordability_check_endpoint():
    """Verify POST /api/business/affordability-check accurately evaluates safety reserve."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Modest purchase ($250.00 = 25000 cents) -> Affordable
        res_ok = await ac.post("/api/business/affordability-check", json={"amount_cents": 25000})
        assert res_ok.status_code == 200
        ok_data = res_ok.json()
        assert ok_data["is_affordable"] is True
        assert ok_data["violates_safety_reserve"] is False
        assert ok_data["safety_reserve_margin_cents"] > 0

        # 2. Huge purchase ($10,000.00 = 1000000 cents) -> Exceeds safety reserve
        res_fail = await ac.post("/api/business/affordability-check", json={"amount_cents": 1000000})
        assert res_fail.status_code == 200
        fail_data = res_fail.json()
        assert fail_data["is_affordable"] is False
        assert fail_data["violates_safety_reserve"] is True
        assert fail_data["safety_reserve_margin_cents"] < 0
        assert "AFFORDABILITY DENIAL" in fail_data["reason"]


@pytest.mark.asyncio
async def test_flow_proposal_creation_gate_a_breach_rejection():
    """
    CRITICAL SAFETY INVARIANT (Gate A):
    A business purchase proposal that causes projected cash to drop below the
    $8,000.00 safety reserve floor must fail closed immediately (HTTP 400).
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unsafe_payload = {
            "title": "Unsafe Luxury Office Rig",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-gpu-cluster",
                    "name": "Dedicated GPU Cluster (1 Month)",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 900000, # $9,000.00
                }
            ],
            "subtotal_cents": 900000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Attempting huge expense that breaches reserve floor",
        }
        res = await ac.post("/api/business/proposals", json=unsafe_payload)
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "Affordability violation (Gate A)" in detail
        assert "breaching the statutory minimum safety reserve" in detail

        # Verify Audit Event recorded SECURITY_BLOCK
        async with AsyncSessionLocal() as session:
            event = (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.module == "BUSINESS",
                        AuditEvent.event_type == AuditEventType.SECURITY_BLOCK,
                    )
                    .order_by(desc(AuditEvent.timestamp))
                )
            ).scalars().first()
            assert event is not None
            assert "Gate A Affordability Denial" in event.summary


# ==============================================================================
# 5. Gate B — Universal Trust Engine & Policy pol-003 Enforcement
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_proposal_creation_affordable_evaluates_pol003():
    """
    Verify an affordable proposal passes Gate A and evaluates against pol-003 in Gate B.
    For whitelisted AWS Cloud under $300 limit, enters AWAITING_APPROVAL.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        valid_payload = {
            "title": "AWS Cloud Compute Capacity Refill",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-aws-ec2",
                    "name": "EC2 Production Reserved Instances",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 15000, # $150.00
                }
            ],
            "subtotal_cents": 15000,
            "tax_cents": 1200,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Affordable cloud compute within monthly budget and safety reserve.",
        }
        res = await ac.post("/api/business/proposals", json=valid_payload)
        assert res.status_code == 201
        data = res.json()
        assert data["module"] == "BUSINESS"
        assert data["total_cents"] == 16200 # $162.00
        # Invariant: Mode A requires explicit human approval
        assert data["state"] == "AWAITING_APPROVAL"
        assert len(data["fingerprint_sha256"]) == 64
        assert data["policy_decision"] == "REQUIRE_APPROVAL"


@pytest.mark.asyncio
async def test_flow_proposal_non_whitelisted_merchant_denied():
    """Verify Gate B denies proposal from non-whitelisted merchant under pol-003."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unauthorized_merchant_payload = {
            "title": "Unauthorized Software Tool",
            "merchant_id": "m-unknown-99",
            "merchant_name": "Shady Software Vendor",
            "items": [
                {
                    "product_id": "prod-tool-99",
                    "name": "Utility Tool",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 5000,
                }
            ],
            "subtotal_cents": 5000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Non-whitelisted merchant attempt",
        }
        res = await ac.post("/api/business/proposals", json=unauthorized_merchant_payload)
        assert res.status_code == 201
        data = res.json()
        # Invariant: Policy denial transitions proposal to REJECTED
        assert data["state"] == "REJECTED"
        assert data["policy_decision"] == "DENY"


@pytest.mark.asyncio
async def test_flow_proposal_exceeding_per_order_ceiling_denied():
    """Verify Gate B denies proposal exceeding pol-003 $300.00 (30,000 cents) ceiling."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        over_cap_payload = {
            "title": "AWS Enterprise Bulk Tier",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-aws-bulk",
                    "name": "Enterprise Bulk Compute",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 35000, # $350.00 > $300.00 ceiling
                }
            ],
            "subtotal_cents": 35000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Exceeds per-order limit",
        }
        res = await ac.post("/api/business/proposals", json=over_cap_payload)
        assert res.status_code == 201
        data = res.json()
        assert data["state"] == "REJECTED"
        assert data["policy_decision"] == "DENY"


# ==============================================================================
# 6. End-to-End Approval, Tamper Detection & CARE Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_proposal_approval_and_care_isolation():
    """
    Verify complete flow:
    1. Create affordable business proposal
    2. Explicit human approval via /api/trust/approve
    3. Proposal state transitions to APPROVED
    4. INVARIANT: Isolation from CARE — ZERO budget reservations created.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create proposal
        prop_payload = {
            "title": "Figma Enterprise Seat",
            "merchant_id": "m-figma-01",
            "merchant_name": "Figma Inc",
            "items": [
                {
                    "product_id": "prod-figma-seat",
                    "name": "Figma Design Seat (Monthly)",
                    "category": "Dev Tools",
                    "quantity": 1,
                    "unit_price_cents": 7500, # $75.00
                }
            ],
            "subtotal_cents": 7500,
            "tax_cents": 600,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Figma tool within budget",
        }
        create_res = await ac.post("/api/business/proposals", json=prop_payload)
        assert create_res.status_code == 201
        prop_data = create_res.json()
        proposal_id = prop_data["id"]
        fingerprint = prop_data["fingerprint_sha256"]
        assert prop_data["state"] == "AWAITING_APPROVAL"

        # 2. Human approval via existing Trust Engine
        approve_res = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "fingerprint_hash": fingerprint,
                "approver_user_id": "usr-alex-01",
            },
        )
        assert approve_res.status_code == 200

        # Verify proposal transitioned to APPROVED
        get_res = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert get_res.status_code == 200
        assert get_res.json()["state"] == "APPROVED"

        # 3. CRITICAL INVARIANT: Isolation from CARE
        # Confirm that no BudgetReservation was created for this business proposal
        async with AsyncSessionLocal() as session:
            res_res = await session.execute(
                select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id)
            )
            care_res = res_res.scalars().all()
            assert len(care_res) == 0, "Business proposals must NEVER create CARE budget reservations!"


@pytest.mark.asyncio
async def test_flow_proposal_item_arithmetic_mismatch_rejected():
    """Verify proposal creation rejects item sum not matching subtotal_cents."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        mismatch_payload = {
            "title": "Mismatched Tool Proposal",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-item-1",
                    "name": "Cloud Item",
                    "category": "Infrastructure",
                    "quantity": 2,
                    "unit_price_cents": 5000, # 2 * 5000 = 10000
                }
            ],
            "subtotal_cents": 8000, # Mismatch!
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Arithmetic mismatch test",
        }
        res = await ac.post("/api/business/proposals", json=mismatch_payload)
        assert res.status_code == 400
        assert "does not match subtotal_cents" in res.json()["detail"]


@pytest.mark.asyncio
async def test_flow_overdue_invoice_accounted_on_day_zero():
    """Verify overdue invoices (due_date in the past) are obligated on Day 0 in the forecast."""
    past_due = datetime.now(timezone.utc) - timedelta(days=10)
    async with AsyncSessionLocal() as session:
        overdue_inv = Invoice(
            id=f"inv-overdue-{uuid.uuid4().hex[:6]}",
            business_profile_id="biz-acme-01",
            counterparty="Overdue Utility Vendor",
            invoice_type="PAYABLE",
            amount_cents=15000, # $150.00
            due_date=past_due,
            status="OVERDUE",
            category="Utilities",
        )
        session.add(overdue_inv)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/business/cashflow")
        assert res.status_code == 200
        data = res.json()
        day_0 = data["forecast_days"][0]
        # Overdue invoice must be included on day 0 payables
        assert day_0["payables_cents"] >= 15000


@pytest.mark.asyncio
async def test_flow_proposal_tampered_fingerprint_rejected():
    """Verify attempting to approve a business proposal with a tampered fingerprint is rejected."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "title": "AWS Support Tier",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-aws-support",
                    "name": "AWS Developer Support",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 5000,
                }
            ],
            "subtotal_cents": 5000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Standard monthly developer support tier",
        }
        create_res = await ac.post("/api/business/proposals", json=prop_payload)
        assert create_res.status_code == 201
        prop_data = create_res.json()
        proposal_id = prop_data["id"]

        # Attempt to approve with a fake tampered hash
        tampered_hash = "f" * 64
        appr_res = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "fingerprint_hash": tampered_hash,
                "approver_user_id": "usr-alex-01",
            },
        )
        assert appr_res.status_code == 400
        assert "Fingerprint mismatch" in appr_res.json()["detail"]


# ==============================================================================
# 7. BUSINESS PayPal Order Lifecycle, Idempotency & Gate A Pre-Dispatch Tests (F-03, F-04)
# ==============================================================================

@pytest.mark.asyncio
async def test_flow_business_paypal_lifecycle_e2e_and_idempotency():
    """
    F-03 Resolution: End-to-end integration test for BUSINESS proposal PayPal order lifecycle:
    1. Create valid BUSINESS proposal via POST /api/business/proposals
    2. Verify proposal enters AWAITING_APPROVAL and requires Mode A approval
    3. Negative check: Unapproved proposal calling create-order fails (HTTP 400)
    4. Approve via POST /api/trust/approve
    5. Call POST /api/payments/{proposal_id}/create-order
    6. Verify resulting transaction has ModuleScope.BUSINESS, state PAYMENT_PENDING, is_simulated
    7. Verify duplicate create-order call returns exact same order idempotently
    8. Capture order via POST /api/payments/{proposal_id}/capture
    9. Verify proposal/transaction lifecycle state and audit events (module == "BUSINESS")
    10. Verify ZERO CARE budget reservations created or modified
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create valid business proposal
        prop_payload = {
            "title": "GitHub Enterprise Team Seat",
            "merchant_id": "m-github-01",
            "merchant_name": "GitHub Enterprise",
            "items": [
                {
                    "product_id": "prod-gh-seat",
                    "name": "GitHub Enterprise Seat (Monthly)",
                    "category": "Dev Tools",
                    "quantity": 1,
                    "unit_price_cents": 21000, # $210.00 <= $300.00 ceiling
                }
            ],
            "subtotal_cents": 21000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Standard monthly GitHub Enterprise developer seat",
        }
        res_create = await ac.post("/api/business/proposals", json=prop_payload)
        assert res_create.status_code == 201
        prop = res_create.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        # 2. Mode A requirement: Proposal must be in AWAITING_APPROVAL
        assert prop["state"] == "AWAITING_APPROVAL"
        assert prop["module"] == "BUSINESS"

        # 3. Negative check: Unapproved proposal CANNOT create payment order
        res_unapproved = await ac.post(
            f"/api/payments/{proposal_id}/create-order",
            json={"proposal_id": proposal_id},
        )
        assert res_unapproved.status_code == 400
        assert "AWAITING_APPROVAL" in res_unapproved.json()["detail"]

        # 4. Human approval via /api/trust/approve
        res_approve = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "fingerprint_hash": fingerprint,
                "approver_user_id": "usr-alex-01",
            },
        )
        assert res_approve.status_code == 200

        # Verify state is APPROVED
        res_prop = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert res_prop.status_code == 200
        assert res_prop.json()["state"] == "APPROVED"

        # 5. Create PayPal Order
        res_order = await ac.post(
            f"/api/payments/{proposal_id}/create-order",
            json={"proposal_id": proposal_id},
        )
        assert res_order.status_code == 200, f"Order creation failed: {res_order.status_code} {res_order.text}"
        order_data = res_order.json()
        paypal_order_id = order_data["order_id"]
        assert paypal_order_id.startswith("ORD-") or paypal_order_id.startswith("SIM-ORD-")
        assert order_data["fingerprint_sha256"] == fingerprint
        assert order_data["total_cents"] == 21000

        # 6. Verify Transaction in database
        async with AsyncSessionLocal() as session:
            tx = (
                await session.execute(
                    select(Transaction).where(Transaction.proposal_id == proposal_id)
                )
            ).scalar_one()
            assert tx.paypal_order_id == paypal_order_id
            assert tx.state == TransactionLifecycleState.PAYMENT_PENDING
            assert tx.total_cents == 21000
            assert tx.reconciliation_status == "ORDER_CREATED"

            # Verify Proposal association
            db_prop = await session.get(PurchaseProposal, proposal_id)
            assert db_prop.module == ModuleScope.BUSINESS

        # 7. Idempotency verification: Duplicate create-order call returns identical order
        res_order_dup = await ac.post(
            f"/api/payments/{proposal_id}/create-order",
            json={"proposal_id": proposal_id},
        )
        assert res_order_dup.status_code == 200
        assert res_order_dup.json()["order_id"] == paypal_order_id

        # 8. Capture order
        res_capture = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
        )
        assert res_capture.status_code == 200
        cap_data = res_capture.json()
        assert cap_data["proposal_id"] == proposal_id
        assert cap_data["fingerprint_sha256"] == fingerprint

        # 9. Verify Audit Events and Proposal State
        res_prop_final = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert res_prop_final.status_code == 200
        final_prop = res_prop_final.json()
        assert final_prop["paypal_order_id"] == paypal_order_id

        async with AsyncSessionLocal() as session:
            # Audit events scoping
            audits = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == proposal_id,
                        AuditEvent.event_type.in_([
                            AuditEventType.PAYPAL_ORDER_CREATED,
                            AuditEventType.PAYPAL_ORDER_CAPTURED,
                        ]),
                    )
                )
            ).scalars().all()
            assert len(audits) >= 2
            for a in audits:
                assert a.module == "BUSINESS"

            # 10. CRITICAL: CARE budget reservation isolation
            res_care = (
                await session.execute(
                    select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id)
                )
            ).scalars().all()
            assert len(res_care) == 0, "Business payments must NEVER create CARE budget reservations!"


@pytest.mark.asyncio
async def test_flow_business_negative_order_creation_paths():
    """Verify rejected proposals and tampered proposals cannot dispatch PayPal orders."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # A. Rejected Proposal: Exceeds pol-003 $300 limit ($350)
        rejected_payload = {
            "title": "AWS Compute Over Cap",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-aws-over",
                    "name": "AWS Bulk Server",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 35000,
                }
            ],
            "subtotal_cents": 35000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Over-cap rejection test",
        }
        res_rej = await ac.post("/api/business/proposals", json=rejected_payload)
        assert res_rej.status_code == 201
        rej_id = res_rej.json()["id"]
        assert res_rej.json()["state"] == "REJECTED"

        # Attempt create-order on REJECTED proposal
        res_rej_order = await ac.post(
            f"/api/payments/{rej_id}/create-order",
            json={"proposal_id": rej_id},
        )
        assert res_rej_order.status_code == 403
        assert "REJECTED" in res_rej_order.json()["detail"]

        # B. Tampered Fingerprint: Modify proposal items directly in DB after approval
        prop_payload = {
            "title": "Figma Seat Valid",
            "merchant_id": "m-figma-01",
            "merchant_name": "Figma Inc",
            "items": [
                {
                    "product_id": "prod-fig-tamper",
                    "name": "Figma Seat",
                    "category": "Dev Tools",
                    "quantity": 1,
                    "unit_price_cents": 4500,
                }
            ],
            "subtotal_cents": 4500,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Tamper test proposal",
        }
        res_tamper = await ac.post("/api/business/proposals", json=prop_payload)
        assert res_tamper.status_code == 201
        tamper_id = res_tamper.json()["id"]
        tamper_fp = res_tamper.json()["fingerprint_sha256"]

        # Approve legitimately
        res_appr = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": tamper_id,
                "fingerprint_hash": tamper_fp,
                "approver_user_id": "usr-alex-01",
            },
        )
        assert res_appr.status_code == 200

        # Now tamper the database row directly
        async with AsyncSessionLocal() as session:
            p = await session.get(PurchaseProposal, tamper_id)
            p.total_cents = 999999 # Malicious modification!
            await session.commit()

        # Attempt create-order with tampered database state
        res_tampered_order = await ac.post(
            f"/api/payments/{tamper_id}/create-order",
            json={"proposal_id": tamper_id},
        )
        assert res_tampered_order.status_code == 400
        assert "Fingerprint integrity failure" in res_tampered_order.json()["detail"]


@pytest.mark.asyncio
async def test_flow_paypal_dispatch_blocked_when_cashflow_deteriorates():
    """
    F-04 Resolution: Verify Gate A affordability is re-evaluated immediately prior
    to PayPal order dispatch.
    If intervening financial obligations (e.g. large payable or cash depletion)
    cause projected cash to breach the $8,000 safety reserve floor:
    1. Order creation is blocked with HTTP 403 Forbidden.
    2. PayPal provider is NOT called.
    3. Proposal remains in APPROVED state (not mutated to PAYMENT_PENDING).
    4. Approval record is preserved.
    5. Audit event with SECURITY_BLOCK and PRE_DISPATCH_GATE_A_RECHECK is recorded.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create affordable proposal: $250.00
        prop_payload = {
            "title": "AWS Compute Reserve Test",
            "merchant_id": "m-aws-01",
            "merchant_name": "AWS Cloud Sandbox",
            "items": [
                {
                    "product_id": "prod-aws-chk",
                    "name": "AWS Cloud Resource",
                    "category": "Infrastructure",
                    "quantity": 1,
                    "unit_price_cents": 25000, # $250.00
                }
            ],
            "subtotal_cents": 25000,
            "tax_cents": 0,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Pre-dispatch affordability recheck test",
        }
        res_create = await ac.post("/api/business/proposals", json=prop_payload)
        assert res_create.status_code == 201
        prop_id = res_create.json()["id"]
        fp = res_create.json()["fingerprint_sha256"]

        # 2. Human approval succeeds
        res_appr = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": prop_id,
                "fingerprint_hash": fp,
                "approver_user_id": "usr-alex-01",
            },
        )
        assert res_appr.status_code == 200

        # 3. Simulate intervening cashflow deterioration:
        # Insert a massive payable invoice ($10,000.00) due tomorrow,
        # which reliably causes projected minimum cash to breach the $8,000.00 reserve floor
        # regardless of any calendar aging or concurrent client receivables!
        due_tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        async with AsyncSessionLocal() as session:
            shock_inv = Invoice(
                id=f"inv-shock-{uuid.uuid4().hex[:6]}",
                business_profile_id="biz-acme-01",
                counterparty="Emergency Tax Assessment",
                invoice_type="PAYABLE",
                amount_cents=1000000, # $10,000.00 shock!
                due_date=due_tomorrow,
                status="PENDING",
                category="Taxes",
            )
            session.add(shock_inv)
            await session.commit()

        # 4. Attempt order creation - Gate A pre-dispatch re-check must intercept and block!
        res_dispatch = await ac.post(
            f"/api/payments/{prop_id}/create-order",
            json={"proposal_id": prop_id},
        )
        assert res_dispatch.status_code == 403
        assert "Payment dispatch rejected by Gate A cashflow engine" in res_dispatch.json()["detail"]

        # 5. Verify state preservation: proposal remains in APPROVED state
        res_prop = await ac.get(f"/api/trust/proposals/{prop_id}")
        assert res_prop.status_code == 200
        assert res_prop.json()["state"] == "APPROVED"

        # 6. Verify Audit Event
        async with AsyncSessionLocal() as session:
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == prop_id,
                        AuditEvent.event_type == AuditEventType.SECURITY_BLOCK,
                    )
                )
            ).scalars().first()
            assert audit is not None
            assert audit.details.get("block_stage") == "PRE_DISPATCH_GATE_A_RECHECK"
            assert audit.decision == "DENY"

            # Verify no transaction was persisted with paypal_order_id
            tx = (
                await session.execute(
                    select(Transaction).where(Transaction.proposal_id == prop_id)
                )
            ).scalar_one_or_none()
            assert tx is None or tx.paypal_order_id is None

            # Clean up shock invoice so it doesn't affect subsequent tests
            inv_to_del = await session.get(Invoice, shock_inv.id)
            if inv_to_del:
                await session.delete(inv_to_del)
                await session.commit()


@pytest.mark.asyncio
async def test_flow_paypal_dispatch_personal_and_care_unaffected():
    """Verify that PERSONAL proposals are not subjected to the Business Gate A cashflow recheck."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unique = uuid.uuid4().hex[:6]
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": f"Test Keyboard {unique}",
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
            "ai_reasoning": "Personal proposal unaffected check",
        }
        res_create = await ac.post("/api/trust/proposals", json=prop_payload)
        assert res_create.status_code == 200
        prop_id = res_create.json()["id"]
        fp = res_create.json()["fingerprint_sha256"]

        # Transition to AWAITING_APPROVAL and approve
        await ac.post(f"/api/trust/transactions/{prop_id}/transition", json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"})
        await ac.post(f"/api/trust/transactions/{prop_id}/transition", json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"})
        res_appr = await ac.post(
            "/api/trust/approve",
            json={"proposal_id": prop_id, "fingerprint_hash": fp, "approver_user_id": "usr-alex-01"},
        )
        assert res_appr.status_code == 200

        # Create PayPal Order - Must succeed without business Gate A check
        res_order = await ac.post(f"/api/payments/{prop_id}/create-order", json={"proposal_id": prop_id})
        assert res_order.status_code == 200, f"Order creation failed: {res_order.text}"
        assert res_order.json()["order_id"] is not None
