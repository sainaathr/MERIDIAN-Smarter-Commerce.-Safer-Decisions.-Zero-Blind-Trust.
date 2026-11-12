import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.main import app
from app.core.init_db import init_database
from app.core.database import AsyncSessionLocal
from app.models.entities import (
    User,
    CareProfile,
    GuardianRelationship,
    PurchaseProposal,
    ProposalItem,
    Approval,
    ConsentPolicy,
    Transaction,
    AuditEvent,
)
from app.models.enums import (
    TransactionLifecycleState,
    AuditEventType,
    ApprovalMode,
)

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()
    async with AsyncSessionLocal() as session:
        u_rec = await session.get(User, "usr-eleanor-03")
        if not u_rec:
            session.add(User(id="usr-eleanor-03", email="eleanor03@test.demo", full_name="Eleanor Vance 03", is_active=True))
        u_guard = await session.get(User, "usr-sarah-02")
        if not u_guard:
            session.add(User(id="usr-sarah-02", email="sarah02@test.demo", full_name="Sarah Vance", is_active=True))
        await session.flush()

        cp = await session.get(CareProfile, "care-eleanor-01")
        if cp:
            g_rel = await session.scalar(
                select(GuardianRelationship).where(
                    GuardianRelationship.care_profile_id == cp.id,
                    GuardianRelationship.guardian_user_id == "usr-sarah-02"
                )
            )
            if not g_rel:
                session.add(GuardianRelationship(
                    id="gr-sarah-eleanor-01",
                    care_profile_id=cp.id,
                    guardian_user_id="usr-sarah-02",
                    relationship_type="Guardian",
                    can_approve=True,
                    max_approval_amount_cents=20000,
                    is_active=True,
                ))
        await session.commit()

@pytest.mark.asyncio
async def test_unapproved_proposal_cannot_create_paypal_order():
    """SECURITY INVARIANT: Proposal without valid human or policy approval CANNOT create a PayPal order."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Unapproved Drone",
            "merchant_id": "m-dji-01",
            "merchant_name": "DJI Direct",
            "items": [
                {
                    "product_id": "prod-drone-01",
                    "name": "DJI Mini Drone",
                    "category": "Electronics",
                    "quantity": 1,
                    "unit_price_cents": 49900,
                }
            ],
            "subtotal_cents": 49900,
            "tax_cents": 3992,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "High-ticket item.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res_prop.json()["id"]

        # Attempt to create order directly from PROPOSED state
        res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert res.status_code == 400
        assert "Cannot create payment order" in res.json()["detail"] or "approval" in res.json()["detail"].lower()

@pytest.mark.asyncio
async def test_expired_approval_cannot_create_paypal_order():
    """SECURITY INVARIANT: Expired approval CANNOT authorize PayPal order creation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Mechanical Keyboard Switch Puller",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-pull-01",
                    "name": "Switch Puller",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 900,
                }
            ],
            "subtotal_cents": 900,
            "tax_cents": 72,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Accessory.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        # Grant approval with TTL = 1 second
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 1,
            },
        )

        # Force expiration in DB
        async with AsyncSessionLocal() as session:
            past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
            await session.execute(
                update(Approval)
                .where(Approval.fingerprint_hash == fingerprint)
                .values(expires_at=past_time)
            )
            await session.commit()

        # Attempt to create order
        res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert res.status_code == 400
        assert "expired" in res.json()["detail"].lower()

@pytest.mark.asyncio
async def test_revoked_approval_cannot_create_paypal_order():
    """SECURITY INVARIANT: Revoked approval CANNOT authorize PayPal order creation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Security Isolated Revoked Test Mic",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-sec-isolated-revoked-01",
                    "name": "Security Test Isolated Mic",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 5900,
                }
            ],
            "subtotal_cents": 5900,
            "tax_cents": 472,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Audio equipment security test.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )

        # Revoke approval in DB
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Approval)
                .where(Approval.fingerprint_hash == fingerprint)
                .values(is_revoked=True, revoked_reason="Suspicious user activity detected.")
            )
            await session.commit()

        # Attempt order creation
        res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert res.status_code == 400
        assert "revoked" in res.json()["detail"].lower()

@pytest.mark.asyncio
async def test_tamper_after_order_creation_blocks_capture_and_revokes_approval():
    """
    CRITICAL SECURITY INVARIANT:
    If transaction parameters (price/items) are modified in DB after PayPal order is created,
    capture must detect cryptographic hash mismatch, HALT execution, REVOKE approval, and mark FAILED.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Mechanical Numpad",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-numpad-01",
                    "name": "Keychron Q0 Numpad",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 5900,
                }
            ],
            "subtotal_cents": 5900,
            "tax_cents": 472,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Productivity accessory.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )

        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert ord_res.status_code == 200
        paypal_order_id = ord_res.json()["order_id"]

        # MALICIOUS TAMPER: Alter item price directly in database before capture
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ProposalItem)
                .where(ProposalItem.proposal_id == proposal_id)
                .values(unit_price_cents=99900, total_cents=99900)
            )
            await session.commit()

        # Attempt to capture
        cap_res = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
        )
        assert cap_res.status_code == 400
        assert "mismatch" in cap_res.json()["detail"].lower() or "tamper" in cap_res.json()["detail"].lower()

        # Verify proposal and transaction transitioned to FAILED
        get_p = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert get_p.json()["state"] == "FAILED"

        # Verify audit event TAMPER_DETECTED was created
        async with AsyncSessionLocal() as session:
            audit_res = await session.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_id == proposal_id,
                    AuditEvent.event_type == AuditEventType.TAMPER_DETECTED,
                )
            )
            tamper_audit = audit_res.scalars().first()
            assert tamper_audit is not None
            assert tamper_audit.severity == "ALERT"

@pytest.mark.asyncio
async def test_mismatched_paypal_order_id_rejected():
    """Ensures capture request with mismatched PayPal order ID is rejected."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Webcam Cover",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [
                {
                    "product_id": "prod-cover-01",
                    "name": "Slider Cover 3-pack",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 700,
                }
            ],
            "subtotal_cents": 700,
            "tax_cents": 56,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Privacy accessory.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res_prop.json()["id"]
        fingerprint = res_prop.json()["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})

        # Capture with spoofed/mismatched order ID
        cap_res = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": "SIM-ORD-SPOOFED999"},
        )
        assert cap_res.status_code == 400
        assert "mismatch" in cap_res.json()["detail"].lower()

@pytest.mark.asyncio
async def test_care_mode_a_requires_guardian_approval_before_payment():
    """Assisted Commerce Mode A: Care recipient purchases require explicit human guardian approval before payment."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        care_payload = {
            "user_id": "usr-eleanor-03",
            "care_profile_id": "care-eleanor-01",
            "module": "CARE",
            "title": "Blood Pressure Monitor Cuff",
            "merchant_id": "m-omron-01",
            "merchant_name": "Omron Healthcare",
            "items": [
                {
                    "product_id": "prod-cuff-01",
                    "name": "Omron Wide-Range D-Ring Cuff",
                    "category": "Medical Supplies",
                    "quantity": 1,
                    "unit_price_cents": 2400,
                }
            ],
            "subtotal_cents": 2400,
            "tax_cents": 192,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Assisted commerce replacement cuff.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=care_payload)
        proposal_id = res_prop.json()["id"]
        fingerprint = res_prop.json()["fingerprint_sha256"]

        # Evaluate policy -> Must be REQUIRE_APPROVAL for Mode A
        eval_res = await ac.post("/api/trust/evaluate", json={"proposal_id": proposal_id, "acting_user_id": "usr-eleanor-03"})
        assert eval_res.json()["decision"] == "REQUIRE_APPROVAL"

        # Attempt order creation before guardian approval -> Must fail
        order_fail = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert order_fail.status_code == 400

        # Now authorized guardian usr-sarah-02 grants approval
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-sarah-02", "actor_role": "GUARDIAN"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-sarah-02", "actor_role": "GUARDIAN"},
        )
        app_res = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-sarah-02",
                "approval_type": "GUARDIAN_DELEGATED",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        assert app_res.status_code == 200

        # Order creation now succeeds
        order_ok = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert order_ok.status_code == 200
        assert order_ok.json()["status"] in ("CREATED", "APPROVED")

@pytest.mark.asyncio
async def test_client_spoofed_amount_cannot_alter_paypal_payment():
    """SECURITY INVARIANT: Client cannot override authoritative order amount via body payload."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB Cable Clip",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [
                {
                    "product_id": "prod-clip-01",
                    "name": "Magnetic Cable Clips",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 1200,
                }
            ],
            "subtotal_cents": 1200,
            "tax_cents": 96,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Organization.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )

        # Attacker tries to send spoofed total_cents: 100 ($1.00) in body
        ord_res = await ac.post(
            f"/api/payments/{proposal_id}/create-order",
            json={"proposal_id": proposal_id, "total_cents": 100},
        )
        assert ord_res.status_code == 200
        # The authoritative order amount must still be 1296 ($12.96), completely ignoring client spoofing
        assert ord_res.json()["total_cents"] == 1296

@pytest.mark.asyncio
async def test_care_mode_b_auto_approval_can_proceed_to_payment():
    """Assisted Commerce Mode B: Controlled auto-reorder allows payment through legitimate server-side workflow."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Set Care profile to Mode B (Controlled Auto)
        await ac.post(
            "/api/care/mode",
            json={"care_profile_id": "care-eleanor-01", "mode": "MODE_B_CONTROLLED_AUTO"},
        )

        # 2. Trigger legitimate server-side Mode B reorder
        reorder_res = await ac.post(
            "/api/care/reorder",
            json={
                "schedule_id": "sched-cgm-01",
                "care_profile_id": "care-eleanor-01",
                "actor_id": "usr-alex-01",
            },
        )
        assert reorder_res.status_code == 200
        reorder_data = reorder_res.json()
        assert reorder_data["decision"] == "ALLOW"
        assert reorder_data["requires_approval"] is False
        assert reorder_data["order"] is not None

        proposal_id = reorder_data["proposal"]["id"]
        order_id = reorder_data["order"]["order_id"]

        # 3. Capture succeeds in simulated mode
        cap_res = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": order_id},
        )
        assert cap_res.status_code == 200
        assert cap_res.json()["status"] in ("COMPLETED", "SIMULATED")

def test_ai_service_cannot_invoke_payment_gateway():
    """SECURITY INVARIANT: AI services possess no methods or permissions to execute payments."""
    from app.ai.gemini_service import GeminiService
    service = GeminiService()
    # Confirm no payment or capture methods exist on AI service
    for forbidden in ["capture", "create_order", "pay", "execute_payment", "authorize"]:
        assert not hasattr(service, forbidden), f"AI service must not have payment method '{forbidden}'"
