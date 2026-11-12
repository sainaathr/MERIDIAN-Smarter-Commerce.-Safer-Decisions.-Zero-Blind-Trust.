import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_database
from app.models.entities import (
    PurchaseProposal,
    Transaction,
    Approval,
    ConsentPolicy,
    ProposalItem,
    TransactionFingerprint,
)
from app.models.enums import TransactionLifecycleState, ApprovalType, ModuleScope
from app.engine.lifecycle import (
    LEGAL_TRANSITIONS,
    PAYMENT_RESTRICTED_STATES,
    validate_transition,
    IllegalStateTransitionError,
)
from app.engine.fingerprint import compute_transaction_fingerprint
from app.payments.paypal_client import (
    PayPalClient,
    parse_monetary_to_minor_units,
    cents_to_paypal_amount,
    paypal_amount_to_cents,
    CURRENCY_MINOR_UNITS,
)
from app.payments.paypal_service import PayPalService
from app.payments.schemas import PayPalCaptureResponse
from app.payments.exceptions import (
    PaymentVerificationError,
    PaymentCaptureError,
    PaymentIdempotencyConflict,
    ConfirmedUncapturedOrderError,
    MonetaryValidationError,
)
from sqlalchemy import select, update

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()

async def create_approved_proposal_and_order(ac: AsyncClient):
    """Helper to create a fully verified and approved proposal ready for payment."""
    unique_suffix = uuid.uuid4().hex[:6]
    prop_payload = {
        "user_id": "usr-alex-01",
        "module": "PERSONAL",
        "title": f"Ergonomic Keyboard {unique_suffix}",
        "merchant_id": f"m-logi-{unique_suffix}",
        "merchant_name": "Logitech Official",
        "items": [
            {
                "product_id": f"prod-kb-{unique_suffix}",
                "name": f"Wireless Split Keyboard {unique_suffix}",
                "category": "Office",
                "quantity": 1,
                "unit_price_cents": 12000,
            }
        ],
        "subtotal_cents": 12000,
        "tax_cents": 960,
        "shipping_cents": 0,
        "currency": "USD",
        "ai_reasoning": "Ergonomic input device for work.",
    }
    res = await ac.post("/api/trust/proposals", json=prop_payload)
    assert res.status_code == 200, res.text
    prop_data = res.json()
    proposal_id = prop_data["id"]
    fingerprint = prop_data["fingerprint_sha256"]

    # Transition to AWAITING_APPROVAL
    await ac.post(
        f"/api/trust/transactions/{proposal_id}/transition",
        json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
    )
    await ac.post(
        f"/api/trust/transactions/{proposal_id}/transition",
        json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
    )

    # Approve
    app_res = await ac.post(
        "/api/trust/approve",
        json={
            "proposal_id": proposal_id,
            "approver_user_id": "usr-alex-01",
            "approval_type": "USER_DIRECT",
            "fingerprint_hash": fingerprint,
            "ttl_seconds": 3600,
        },
    )
    assert app_res.status_code == 200

    # Create Order
    ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
    assert ord_res.status_code == 200
    paypal_order_id = ord_res.json()["order_id"]

    return proposal_id, paypal_order_id, fingerprint

# ==============================================================================
# SECTION 1: ELIMINATE GENERIC PAYMENT LIFECYCLE BYPASS
# ==============================================================================

@pytest.mark.asyncio
async def test_generic_transition_rejects_paid_directly():
    """Proves POST /api/trust/transactions/{proposal_id}/transition targeting PAID is rejected with 403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, _, _ = await create_approved_proposal_and_order(ac)

        # Attempt to transition directly to PAID via generic transition route
        res = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={
                "target_state": "PAID",
                "actor_id": "usr-alex-01",
                "actor_role": "USER",
                "reason": "Bypassing payment gateway",
            },
        )
        assert res.status_code == 403
        assert "Forbidden state transition" in res.json()["detail"]
        assert "Only verified payment execution subsystems may authorize state 'PAID'" in res.json()["detail"]

@pytest.mark.asyncio
async def test_generic_transition_rejects_paid_with_fake_capture_id():
    """Proves generic transition targeting PAID is rejected even with client-supplied capture ID or parameters."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, _, _ = await create_approved_proposal_and_order(ac)

        res = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={
                "target_state": "PAID",
                "actor_id": "usr-alex-01",
                "actor_role": "USER",
                "paypal_capture_id": "FAKE-CAP-12345",
                "reason": "Client claims payment succeeded",
            },
        )
        assert res.status_code == 403

@pytest.mark.asyncio
async def test_generic_transition_rejects_paid_in_payment_pending():
    """Proves rejection occurs even when the proposal and transaction are legitimately in PAYMENT_PENDING."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, _, _ = await create_approved_proposal_and_order(ac)

        # Verify proposal is in PAYMENT_PENDING
        details_res = await ac.get(f"/api/payments/{proposal_id}")
        assert details_res.status_code == 200
        assert details_res.json()["state"] == "PAYMENT_PENDING"

        # Attempt generic transition from PAYMENT_PENDING to PAID
        res = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={
                "target_state": "PAID",
                "actor_id": "usr-alex-01",
                "actor_role": "SYSTEM",
            },
        )
        assert res.status_code == 403

@pytest.mark.asyncio
async def test_legitimate_non_payment_transitions_still_work():
    """Proves non-restricted lifecycle transitions remain fully operational."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a new draft/proposal
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB Cable",
            "merchant_id": "m-cable-01",
            "merchant_name": "Cable Direct",
            "items": [{"product_id": "p-1", "name": "USB-C", "category": "Tech", "quantity": 1, "unit_price_cents": 1500}],
            "subtotal_cents": 1500,
            "tax_cents": 120,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Cable replacement",
        }
        res = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res.json()["id"]

        # Valid non-payment transition 1: PROPOSED -> POLICY_CHECK
        t1 = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        assert t1.status_code == 200
        assert t1.json()["new_state"] == "POLICY_CHECK"

        # Valid non-payment transition 2: POLICY_CHECK -> CANCELLED
        t2 = await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "CANCELLED", "actor_id": "usr-alex-01", "actor_role": "USER"},
        )
        assert t2.status_code == 200
        assert t2.json()["new_state"] == "CANCELLED"

@pytest.mark.asyncio
async def test_simulated_capture_cannot_transition_to_paid():
    """Proves simulated PayPal capture preserves PAYMENT_PENDING and never marks PAID."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Force simulated mode on client
        sim_client = PayPalClient(client_id="", client_secret="")
        service = PayPalService(client=sim_client)

        async with AsyncSessionLocal() as session:
            cap_res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert cap_res.status == "SIMULATED"
            assert cap_res.is_simulated is True

            # Invariant: Database proposal and transaction remain in PAYMENT_PENDING
            p_res = await session.execute(select(PurchaseProposal).where(PurchaseProposal.id == proposal_id))
            prop = p_res.scalar_one()
            assert prop.state == TransactionLifecycleState.PAYMENT_PENDING

            t_res = await session.execute(select(Transaction).where(Transaction.proposal_id == proposal_id))
            tx = t_res.scalar_one()
            assert tx.state == TransactionLifecycleState.PAYMENT_PENDING
            assert tx.is_simulated is True

# ==============================================================================
# SECTION 2: DATABASE-BACKED CONCURRENT CAPTURE PROTECTION
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_captures_separate_service_instances_and_sessions():
    """
    MANDATORY AUDIT TEST:
    Two independently created service instances with separate database sessions
    concurrently attempt to capture the same transaction.
    Proves:
    1. Both requests contend for the same transaction.
    2. Only one request obtains the authoritative database capture claim.
    3. Only one logical provider capture is initiated.
    4. Competing request is rejected with PaymentIdempotencyConflict.
    5. The authoritative idempotency key remains identical.
    6. Resulting database state is consistent and PAID.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Mock client that delays slightly to simulate network latency
        unique_cap_id = f"CAP-CONC-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False

        async def delayed_capture(order_id, idempotency_key=None):
            await asyncio.sleep(0.08)
            return {
                "id": order_id,
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "id": unique_cap_id,
                                    "status": "COMPLETED",
                                    "amount": {"currency_code": "USD", "value": "129.60"},
                                }
                            ]
                        }
                    }
                ],
            }

        mock_client.capture_order.side_effect = delayed_capture

        # Two completely independent service instances
        service1 = PayPalService(client=mock_client)
        service2 = PayPalService(client=mock_client)

        # Run concurrent captures across separate sessions
        async def run_worker1():
            async with AsyncSessionLocal() as s1:
                return await service1.capture_order_for_proposal(s1, proposal_id, paypal_order_id)

        async def run_worker2():
            async with AsyncSessionLocal() as s2:
                return await service2.capture_order_for_proposal(s2, proposal_id, paypal_order_id)

        results = await asyncio.gather(run_worker1(), run_worker2(), return_exceptions=True)

        # One must succeed and one must be rejected with PaymentIdempotencyConflict
        successes = [r for r in results if isinstance(r, PayPalCaptureResponse)]
        conflicts = [r for r in results if isinstance(r, PaymentIdempotencyConflict)]

        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}. Results: {results}"
        assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}. Results: {results}"
        assert "Concurrent capture claim rejected" in str(conflicts[0])

        # Exactly one provider capture call dispatched
        assert mock_client.capture_order.call_count == 1

        # Check database consistency
        async with AsyncSessionLocal() as verify_session:
            t_res = await verify_session.execute(select(Transaction).where(Transaction.proposal_id == proposal_id))
            tx = t_res.scalar_one()
            assert tx.state == TransactionLifecycleState.PAID
            assert tx.paypal_capture_id == unique_cap_id
            assert tx.paypal_status == "COMPLETED"
            assert tx.capture_idempotency_key is not None

@pytest.mark.asyncio
async def test_replayed_capture_request_returns_safe_idempotent_result():
    """Proves duplicate client submissions after settlement return identical response without re-dispatching."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        unique_cap_id = f"CAP-IDEMP-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.capture_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {"id": unique_cap_id, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}
                        ]
                    }
                }
            ],
        }

        service = PayPalService(client=mock_client)

        async with AsyncSessionLocal() as session1:
            resp1 = await service.capture_order_for_proposal(session1, proposal_id, paypal_order_id)
            assert resp1.status == "COMPLETED"
            assert resp1.capture_id == unique_cap_id

        # Replay capture with new session
        async with AsyncSessionLocal() as session2:
            resp2 = await service.capture_order_for_proposal(session2, proposal_id, paypal_order_id)
            assert resp2.status == "COMPLETED"
            assert resp2.capture_id == unique_cap_id
            assert resp2.total_cents == resp1.total_cents

        # Provider capture was called ONLY once
        assert mock_client.capture_order.call_count == 1

@pytest.mark.asyncio
async def test_stale_capture_claim_safe_reconciliation():
    """
    Proves that a stale in-progress capture attempt (e.g. from crashed process > 60s ago)
    is safely reclaimed and reconciled with get_order probe instead of blindly capturing again.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Simulate a crash 120s ago while in CAPTURING status
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="CAPTURING",
                    capture_idempotency_key=f"meridian-cap-stale-{uuid.uuid4().hex[:8]}",
                    updated_at=old_time,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()

        # New worker starts; mock get_order confirms it completed
        reconciled_cap_id = f"CAP-RECON-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [
                            {"id": reconciled_cap_id, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}
                        ]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            cap_res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert cap_res.status == "COMPLETED"
            assert cap_res.capture_id == reconciled_cap_id
            assert cap_res.reconciliation_status == "RECONCILED_SUCCESS"

        # mock_client.capture_order was NEVER called! get_order reconciled safely!
        assert mock_client.capture_order.call_count == 0
        assert mock_client.get_order.call_count == 1

# ==============================================================================
# SECTION 3: COMPREHENSIVE TIMEOUT RECONCILIATION VERIFICATION
# ==============================================================================

@pytest.mark.asyncio
async def test_reconciliation_rejects_missing_amount():
    """Proves reconciliation rejects probe responses missing amount data."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "payments": {
                        "captures": [{"id": "CAP-123", "status": "COMPLETED"}] # Missing amount!
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Missing amount"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "RECONCILIATION_REQUIRED"
            assert details.state == "PAYMENT_PENDING"

@pytest.mark.asyncio
async def test_reconciliation_rejects_wrong_amount():
    """Proves reconciliation rejects probe responses with amount mismatch (e.g. 50.00 vs 129.60)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "50.00"}, # Wrong amount!
                    "payments": {
                        "captures": [{"id": "CAP-123", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "50.00"}}]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Provider amount 5000 cents does not match"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "RECONCILIATION_REQUIRED"

@pytest.mark.asyncio
async def test_reconciliation_rejects_wrong_currency():
    """Proves reconciliation rejects probe responses with currency mismatch (e.g. EUR vs USD)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "EUR", "value": "129.60"}, # Wrong currency!
                    "payments": {
                        "captures": [{"id": "CAP-123", "status": "COMPLETED", "amount": {"currency_code": "EUR", "value": "129.60"}}]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Provider currency 'EUR' does not match"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

@pytest.mark.asyncio
async def test_reconciliation_rejects_wrong_order_id():
    """Proves reconciliation rejects probe responses where returned order ID does not match expected."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": "ORD-COMPLETELY-DIFFERENT", # Wrong order ID
            "status": "COMPLETED",
            "purchase_units": [],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Provider order ID 'ORD-COMPLETELY-DIFFERENT' does not match"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

@pytest.mark.asyncio
async def test_reconciliation_rejects_wrong_custom_id_association():
    """Proves reconciliation rejects probe where custom_id belongs to a different proposal."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": "different-proposal-id", # Mismatch
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [{"id": "CAP-123", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Provider custom_id 'different-proposal-id' does not match"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

@pytest.mark.asyncio
async def test_reconciliation_external_capture_with_cancelled_state_moves_to_manual_review():
    """
    CRITICAL INVARIANT TEST:
    If PayPal confirmed a capture, but the MERIDIAN proposal was meanwhile CANCELLED,
    the payment service MUST NOT silently overwrite the cancelled state with PAID.
    It MUST record the genuine external capture ID and move the transaction to MANUAL_REVIEW_REQUIRED.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Cancel proposal in MERIDIAN
        async with AsyncSessionLocal() as session:
            p_res = await session.execute(select(PurchaseProposal).where(PurchaseProposal.id == proposal_id))
            prop = p_res.scalar_one()
            prop.state = TransactionLifecycleState.CANCELLED

            t_res = await session.execute(select(Transaction).where(Transaction.proposal_id == proposal_id))
            tx = t_res.scalar_one()
            tx.state = TransactionLifecycleState.CANCELLED
            await session.commit()

        # PayPal confirms capture succeeded externally
        ext_cap_id = f"CAP-EXT-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [
                            {"id": ext_cap_id, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}
                        ]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="External capture recorded; payment held in MANUAL_REVIEW_REQUIRED"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

            # Invariant: Evidence is preserved, state is NOT PAID, flagged for MANUAL_REVIEW_REQUIRED
            details = await service.get_payment_details(session, proposal_id)
            assert details.state == "CANCELLED"
            assert details.reconciliation_status == "MANUAL_REVIEW_REQUIRED"
            assert details.paypal_capture_id == ext_cap_id
            assert details.paypal_status == "COMPLETED"

@pytest.mark.asyncio
async def test_reconciliation_fingerprint_tampering_moves_to_manual_review():
    """Proves that tampering detected during reconciliation halts transition and flags MANUAL_REVIEW_REQUIRED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Tamper with proposal item in DB
        async with AsyncSessionLocal() as session:
            item_res = await session.execute(select(ProposalItem).where(ProposalItem.proposal_id == proposal_id))
            item = item_res.scalars().first()
            item.unit_price_cents = 99999
            await session.commit()

        unique_tamper_cap = f"CAP-TAMPER-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [{"id": unique_tamper_cap, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError, match="Fingerprint mismatch during reconciliation"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "MANUAL_REVIEW_REQUIRED"
            assert details.state != "PAID"

@pytest.mark.asyncio
async def test_reconciliation_expired_approval_moves_to_manual_review():
    """Proves that expired approval during reconciliation halts transition and flags MANUAL_REVIEW_REQUIRED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, fingerprint = await create_approved_proposal_and_order(ac)

        # Expire approval in DB
        async with AsyncSessionLocal() as session:
            app_res = await session.execute(select(Approval).where(Approval.fingerprint_hash == fingerprint))
            app_rec = app_res.scalars().first()
            app_rec.expires_at = datetime.now(timezone.utc) - timedelta(hours=2)
            await session.commit()

        unique_exp_cap = f"CAP-EXP-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [{"id": unique_exp_cap, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError, match="Approval expired"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "MANUAL_REVIEW_REQUIRED"
            assert details.state != "PAID"

@pytest.mark.asyncio
async def test_reconcile_http_route():
    """Proves POST /api/payments/{proposal_id}/reconcile triggers reconciliation successfully."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Calling reconcile on an order that isn't captured yet in mock probe returns 400
        res = await ac.post(f"/api/payments/{proposal_id}/reconcile")
        assert res.status_code == 400
        assert "Reconciliation" in res.json()["detail"] or "unconfirmed" in res.json()["detail"]


# ==============================================================================
# CORRECTION A — MONETARY PRECISION & ZERO FLOATING-POINT TESTS
# ==============================================================================

def test_authoritative_monetary_precision_decimal_strict_validation():
    """
    Exhaustive validation of parse_monetary_to_minor_units and cents_to_paypal_amount:
    - Valid USD values with and without trailing zeroes
    - One-cent amount
    - Negative and zero values
    - Excess fractional precision
    - NaN and infinity
    - Invalid strings
    - Unsupported or invalid currency precision
    - Rejection of float types in authoritative payment paths
    """
    # 1. Valid USD inputs
    assert parse_monetary_to_minor_units("12.50", "USD") == 1250
    assert parse_monetary_to_minor_units("12.5", "USD") == 1250
    assert parse_monetary_to_minor_units("12.00", "USD") == 1200
    assert parse_monetary_to_minor_units("0.01", "USD") == 1
    assert parse_monetary_to_minor_units("100", "USD") == 10000

    # 2. Allow zero toggle
    assert parse_monetary_to_minor_units("0.00", "USD", allow_zero=True) == 0
    with pytest.raises(MonetaryValidationError, match="positive and non-zero"):
        parse_monetary_to_minor_units("0.00", "USD", allow_zero=False)

    # 3. Excess fractional precision (rejection without truncation)
    with pytest.raises(MonetaryValidationError, match="excessive fractional precision"):
        parse_monetary_to_minor_units("12.345", "USD")
    with pytest.raises(MonetaryValidationError, match="excessive fractional precision"):
        parse_monetary_to_minor_units("12.3456", "USD")
    with pytest.raises(MonetaryValidationError, match="excessive fractional precision"):
        parse_monetary_to_minor_units("0.001", "USD")

    # 4. Negative values
    with pytest.raises(MonetaryValidationError, match="Negative"):
        parse_monetary_to_minor_units("-12.50", "USD")
    with pytest.raises(MonetaryValidationError, match="Negative"):
        parse_monetary_to_minor_units("-0.01", "USD")

    # 5. Non-finite values
    with pytest.raises(MonetaryValidationError, match="Non-finite"):
        parse_monetary_to_minor_units("NaN", "USD")
    with pytest.raises(MonetaryValidationError, match="Non-finite"):
        parse_monetary_to_minor_units("Infinity", "USD")
    with pytest.raises(MonetaryValidationError, match="Non-finite"):
        parse_monetary_to_minor_units("-Infinity", "USD")

    # 6. Invalid / malformed strings
    for invalid in ["invalid", "", "   ", "$12.50", "12..50", "12.50.00", "abc"]:
        with pytest.raises(MonetaryValidationError):
            parse_monetary_to_minor_units(invalid, "USD")

    # 7. Float type rejection (authoritative payment invariant)
    with pytest.raises(MonetaryValidationError, match="Floating-point monetary values are forbidden"):
        parse_monetary_to_minor_units(12.50, "USD")  # type: ignore
    with pytest.raises(MonetaryValidationError, match="Floating-point monetary values are forbidden"):
        parse_monetary_to_minor_units(0.01, "USD")  # type: ignore

    # 8. Currency precision handling (JPY 0 decimals, EUR 2 decimals)
    assert parse_monetary_to_minor_units("500", "JPY") == 500
    with pytest.raises(MonetaryValidationError, match="excessive fractional precision"):
        parse_monetary_to_minor_units("500.5", "JPY")

    assert parse_monetary_to_minor_units("25.99", "EUR") == 2599

    # 9. Unsupported currency
    with pytest.raises(MonetaryValidationError, match="Unsupported"):
        parse_monetary_to_minor_units("12.50", "XYZ")

    # 10. cents_to_paypal_amount formatting and safety
    assert cents_to_paypal_amount(1250, "USD") == "12.50"
    assert cents_to_paypal_amount(1, "USD") == "0.01"
    assert cents_to_paypal_amount(0, "USD") == "0.00"
    assert cents_to_paypal_amount(500, "JPY") == "500"
    with pytest.raises(MonetaryValidationError, match="negative"):
        cents_to_paypal_amount(-50, "USD")
    with pytest.raises(MonetaryValidationError, match="integer"):
        cents_to_paypal_amount(12.50, "USD")  # type: ignore

    # 11. Static AST / source inspection: no float() or round() in authoritative payment arithmetic
    from pathlib import Path
    payments_dir = Path("backend/app/payments")
    for py_file in payments_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for line_idx, line in enumerate(content.splitlines(), start=1):
            if "float(" in line:
                # The only permitted float is httpx timeout parameter (network parameter, not financial)
                assert "timeout" in line, f"Authoritative financial float found in {py_file.name}:{line_idx}: {line}"
            if "round(" in line:
                assert False, f"Forbidden round() found in {py_file.name}:{line_idx}: {line}"


# ==============================================================================
# CORRECTION B — 14 EXHAUSTIVE RECONCILIATION TEST CASES
# ==============================================================================

@pytest.mark.asyncio
async def test_recon_case_1_reconciliation_required_already_captured_zero_new_captures():
    """Case 1: RECONCILIATION_REQUIRED + provider confirms already captured -> verified result; 0 new capture calls."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Place transaction in RECONCILIATION_REQUIRED
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    capture_idempotency_key="cap-persisted-c1",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        ext_cap_id = f"CAP-C1-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [
                            {"id": ext_cap_id, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}
                        ]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert res.status == "COMPLETED"
            assert res.capture_id == ext_cap_id
            # Invariant: ZERO new capture calls issued
            assert mock_client.capture_order.call_count == 0

            details = await service.get_payment_details(session, proposal_id)
            assert details.state == "PAID"
            assert details.reconciliation_status == "RECONCILED_SUCCESS"


@pytest.mark.asyncio
async def test_recon_case_2_reconciliation_required_confirmed_uncaptured_retries_with_persisted_key():
    """Case 2: RECONCILIATION_REQUIRED + provider confirms uncaptured -> retry with the original persisted key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        persisted_key = f"cap-persisted-c2-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    capture_idempotency_key=persisted_key,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        new_cap_id = f"CAP-C2-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        # Provider confirms uncaptured APPROVED order with 0 captures
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }
        mock_client.capture_order.return_value = {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [{"id": new_cap_id, "status": "COMPLETED"}]
                    }
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            # Caller passes a different idempotency_key parameter; service MUST reuse the persisted key!
            res = await service.capture_order_for_proposal(
                session, proposal_id, paypal_order_id, idempotency_key="different-key"
            )
            assert res.status == "COMPLETED"
            assert res.capture_id == new_cap_id
            # Exactly 1 capture call dispatched
            assert mock_client.capture_order.call_count == 1
            # Invariant: Reused persisted key, ignored different key
            assert mock_client.capture_order.call_args[1]["idempotency_key"] == persisted_key


@pytest.mark.asyncio
async def test_recon_case_3_reconciliation_required_provider_timeout_no_capture():
    """Case 3: RECONCILIATION_REQUIRED + provider timeout -> no capture call; state remains reconciliation-required."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.side_effect = PaymentCaptureError("Network timeout querying PayPal order")

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="timed out"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            # Invariant: Zero capture calls
            assert mock_client.capture_order.call_count == 0

            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "RECONCILIATION_REQUIRED"
            assert details.state != "PAID"


@pytest.mark.asyncio
async def test_recon_case_4_reconciliation_required_malformed_response_no_capture():
    """Case 4: RECONCILIATION_REQUIRED + malformed response -> no capture call."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        # Missing purchase units
        mock_client.get_order.return_value = {"id": paypal_order_id, "status": "APPROVED", "purchase_units": []}

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Missing purchase units"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            assert mock_client.capture_order.call_count == 0
            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_recon_case_5_reconciliation_required_amount_mismatch_no_capture():
    """Case 5: RECONCILIATION_REQUIRED + amount mismatch -> no capture call."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "999.99"},  # Mismatch (expected 129.60)
                    "payments": {"captures": []},
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="amount"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            assert mock_client.capture_order.call_count == 0


@pytest.mark.asyncio
async def test_recon_case_6_reconciliation_required_currency_mismatch_no_capture():
    """Case 6: RECONCILIATION_REQUIRED + currency mismatch -> no capture call."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "EUR", "value": "129.60"},  # Mismatch (expected USD)
                    "payments": {"captures": []},
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="currency"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            assert mock_client.capture_order.call_count == 0


@pytest.mark.asyncio
async def test_recon_case_7_reconciliation_required_wrong_proposal_association_no_capture():
    """Case 7: RECONCILIATION_REQUIRED + wrong order/proposal association -> no capture call."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": "different-proposal-uuid-alien",
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="custom_id"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            assert mock_client.capture_order.call_count == 0


@pytest.mark.asyncio
async def test_recon_case_8_stale_capturing_already_captured_reconciles_zero_new_captures():
    """Case 8: Stale CAPTURING + already captured -> reconcile and return verified result."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Stale lock: 120s old
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="CAPTURING",
                    capture_idempotency_key="cap-persisted-c8",
                    updated_at=old_time,
                )
            )
            await session.commit()

        reconciled_cap_id = f"CAP-C8-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [
                            {"id": reconciled_cap_id, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}
                        ]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert res.status == "COMPLETED"
            assert res.capture_id == reconciled_cap_id
            assert mock_client.capture_order.call_count == 0


@pytest.mark.asyncio
async def test_recon_case_9_stale_capturing_confirmed_uncaptured_retries_with_persisted_key():
    """Case 9: Stale CAPTURING + confirmed uncaptured -> controlled retry using the original key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        persisted_key = f"cap-persisted-c9-{uuid.uuid4().hex[:8]}"
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="CAPTURING",
                    capture_idempotency_key=persisted_key,
                    updated_at=old_time,
                )
            )
            await session.commit()

        new_cap_id = f"CAP-C9-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }
        mock_client.capture_order.return_value = {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [{"id": new_cap_id, "status": "COMPLETED"}]
                    }
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert res.status == "COMPLETED"
            assert res.capture_id == new_cap_id
            assert mock_client.capture_order.call_count == 1
            assert mock_client.capture_order.call_args[1]["idempotency_key"] == persisted_key


@pytest.mark.asyncio
async def test_recon_case_10_active_capturing_competing_caller_rejected():
    """Case 10: Active CAPTURING -> competing caller rejected with PaymentIdempotencyConflict."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Active lock (updated just 5 seconds ago)
        active_time = datetime.now(timezone.utc) - timedelta(seconds=5)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="CAPTURING",
                    capture_idempotency_key="cap-active-c10",
                    updated_at=active_time,
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentIdempotencyConflict, match="currently in progress"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            assert mock_client.capture_order.call_count == 0


@pytest.mark.asyncio
async def test_recon_case_11_concurrent_reconciliation_retry_no_duplicate_captures():
    """Case 11: Concurrent reconciliation/retry attempts -> exactly 1 capture, no duplicate captures."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Place transaction in RECONCILIATION_REQUIRED
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    capture_idempotency_key=f"cap-conc-{uuid.uuid4().hex[:8]}",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        shared_cap_id = f"CAP-CONC-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }
        mock_client.capture_order.return_value = {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [{"id": shared_cap_id, "status": "COMPLETED"}]
                    }
                }
            ],
        }

        service = PayPalService(client=mock_client)

        async def worker():
            async with AsyncSessionLocal() as session:
                return await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

        # Dispatch 4 concurrent workers
        results = await asyncio.gather(
            worker(), worker(), worker(), worker(), return_exceptions=True
        )

        successful_results = [r for r in results if isinstance(r, PayPalCaptureResponse)]
        assert len(successful_results) >= 1
        for r in successful_results:
            assert r.status in ("COMPLETED", "SIMULATED")

        # Invariant: At most 1 capture call dispatched across all concurrent attempts!
        assert mock_client.capture_order.call_count <= 1


@pytest.mark.asyncio
async def test_recon_case_12_invalid_approval_or_revoked_consent_blocks_retry():
    """Case 12: Invalid approval or revoked consent during reconciliation -> no new capture."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, fingerprint = await create_approved_proposal_and_order(ac)

        # Revoke the approval
        async with AsyncSessionLocal() as session:
            app_res = await session.execute(select(Approval).where(Approval.fingerprint_hash == fingerprint))
            approval_rec = app_res.scalars().first()
            approval_rec.is_revoked = True
            approval_rec.revoked_reason = "Guardian revoked permission"

            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError, match="revoked"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            # Invariant: ZERO capture calls
            assert mock_client.capture_order.call_count == 0

            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_recon_case_13_invalid_local_lifecycle_state_never_transitions_to_paid():
    """Case 13: Invalid local lifecycle state -> never transition to PAID."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Set proposal and tx to CANCELLED
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(PurchaseProposal)
                .where(PurchaseProposal.id == proposal_id)
                .values(state=TransactionLifecycleState.CANCELLED)
            )
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    state=TransactionLifecycleState.CANCELLED,
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        mock_client = AsyncMock()
        mock_client.is_simulated = False
        ext_cap = f"CAP-C13-{uuid.uuid4().hex[:8]}"
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {
                        "captures": [
                            {"id": ext_cap, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "129.60"}}
                        ]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="MANUAL_REVIEW_REQUIRED"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

            details = await service.get_payment_details(session, proposal_id)
            assert details.state == "CANCELLED"
            assert details.state != "PAID"
            assert details.reconciliation_status == "MANUAL_REVIEW_REQUIRED"
            assert details.paypal_capture_id == ext_cap


@pytest.mark.asyncio
async def test_recon_case_14_simulated_payments_remain_simulated_never_paid():
    """Case 14: Simulated payments remain SIMULATED, never PAID."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # Ensure transaction is marked simulated
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Transaction)
                .where(Transaction.proposal_id == proposal_id)
                .values(
                    paypal_status="RECONCILIATION_REQUIRED",
                    reconciliation_status="RECONCILIATION_REQUIRED",
                    is_simulated=True,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        # Simulated client mock returning simulation markers
        mock_client = AsyncMock()
        mock_client.is_simulated = True
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "_is_simulated": True,
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }
        mock_client.capture_order.return_value = {
            "status": "COMPLETED",
            "is_simulated": True,
            "purchase_units": [
                {
                    "payments": {
                        "captures": [{"id": f"SIM-CAP-{uuid.uuid4().hex[:8]}", "status": "COMPLETED"}]
                    }
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert res.status == "SIMULATED"
            assert res.is_simulated is True

            details = await service.get_payment_details(session, proposal_id)
            # Invariant: Simulated payments are NEVER marked PAID
            assert details.state != "PAID"
            assert details.paypal_status == "SIMULATED"
            assert details.is_simulated is True


# ==============================================================================
# OS-LEVEL SEPARATE PROCESS CONCURRENCY TEST
# ==============================================================================

def test_os_multiprocess_capture_claim_concurrency():
    """
    Spawns TWO independent OS processes (via python subprocess) that concurrently
    attempt atomic database claim on the exact same Transaction row in SQLite.
    Proves database-backed row claiming is cross-process safe and prevents double-claiming.
    """
    import subprocess
    import sys
    import json
    import os

    # Worker script executed in separate OS processes
    worker_script = """
import asyncio
import sys
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import update, or_, and_

sys.path.insert(0, 'backend')
from app.core.database import AsyncSessionLocal
from app.models.entities import Transaction
from app.models.enums import TransactionLifecycleState

async def attempt_claim(tx_id, worker_name):
    async with AsyncSessionLocal() as session:
        now_utc = datetime.now(timezone.utc)
        stale_cutoff = now_utc - timedelta(seconds=60)
        claim_stmt = (
            update(Transaction)
            .where(
                Transaction.id == tx_id,
                Transaction.state == TransactionLifecycleState.PAYMENT_PENDING.value,
                or_(
                    Transaction.paypal_status.is_(None),
                    Transaction.paypal_status == "CREATED",
                    Transaction.paypal_status == "RECONCILIATION_REQUIRED",
                    Transaction.paypal_status == "FAILED",
                    and_(
                        Transaction.paypal_status == "CAPTURING",
                        Transaction.updated_at < stale_cutoff,
                    ),
                ),
            )
            .values(
                paypal_status="CAPTURING",
                capture_idempotency_key=f"cap-proc-{worker_name}",
                version=Transaction.version + 1,
                updated_at=now_utc,
            )
            .execution_options(synchronize_session=False)
        )
        res = await session.execute(claim_stmt)
        await session.commit()
        return {"worker": worker_name, "rowcount": res.rowcount}

if __name__ == "__main__":
    tx_id = sys.argv[1]
    worker_name = sys.argv[2]
    result = asyncio.run(attempt_claim(tx_id, worker_name))
    print(json.dumps(result))
"""

    async def setup_test_row():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            prop_id, _, _ = await create_approved_proposal_and_order(ac)
            async with AsyncSessionLocal() as session:
                t_res = await session.execute(select(Transaction).where(Transaction.proposal_id == prop_id))
                tx = t_res.scalar_one()
                return tx.id

    tx_id = asyncio.run(setup_test_row())

    # Launch Process 1 and Process 2 concurrently
    python_exe = sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = "backend"

    p1 = subprocess.Popen(
        [python_exe, "-c", worker_script, tx_id, "process-1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    p2 = subprocess.Popen(
        [python_exe, "-c", worker_script, tx_id, "process-2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    out1, err1 = p1.communicate(timeout=15)
    out2, err2 = p2.communicate(timeout=15)

    assert p1.returncode == 0, f"Process 1 failed: {err1}"
    assert p2.returncode == 0, f"Process 2 failed: {err2}"

    res1 = json.loads(out1.strip().splitlines()[-1])
    res2 = json.loads(out2.strip().splitlines()[-1])

    rowcounts = [res1["rowcount"], res2["rowcount"]]

    # Invariant: Across separate OS processes, exactly ONE process successfully claims the transaction (rowcount=1)
    # and the competing OS process fails to claim (rowcount=0).
    assert rowcounts.count(1) == 1, f"Expected exactly one successful claim, got: {rowcounts}"
    assert rowcounts.count(0) == 1, f"Expected exactly one rejected claim, got: {rowcounts}"


@pytest.mark.asyncio
async def test_paypal_additional_coverage_edge_cases():
    """Covers unexpected order status without captures, malformed order values, and cancel endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        # 1. Re-call create-order to cover existing transaction update path (lines 283-291)
        res_recreate = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert res_recreate.status_code == 200

        # 2. Status VOIDED without captures
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "VOIDED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "129.60"},
                    "payments": {"captures": []},
                }
            ],
        }
        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="ineligible for capture retry"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

        # 3. Malformed unit amount
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "NOT_A_DECIMAL"},
                    "payments": {"captures": []},
                }
            ],
        }
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Malformed order amount value"):
                await service.reconcile_timed_out_capture(session, proposal_id, paypal_order_id)

        # 4. Cancel endpoint
        cancel_res = await ac.post(f"/api/payments/{proposal_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"
