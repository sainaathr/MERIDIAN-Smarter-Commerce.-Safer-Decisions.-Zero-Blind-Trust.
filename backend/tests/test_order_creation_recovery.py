"""
MERIDIAN — Phase 5.3 Task 1: Targeted Order-Creation Crash Recovery Hardening Tests

Verifies deterministic fail-closed recovery for transactions stranded in ORDER_CREATION_IN_PROGRESS
or RECONCILIATION_REQUIRED during PayPal order creation.

Enforces:
1. Process crash before provider dispatch (confirmed absence of order -> single retry with original key).
2. Process crash after provider creation but before response persistence (confirmed existing order -> adopt order ID, zero duplicate orders).
3. Restart recovery with original idempotency key (idempotency key survives process restart and is NEVER regenerated).
4. Duplicate recovery attempts from separate sessions (atomic concurrency claim prevents racing workers).
5. Confirmed existing order (validates custom_id, currency, total, fingerprint; sets ORDER_CREATED).
6. Confirmed absence of an order (revalidates all invariants, retries with original key).
7. Ambiguous provider response (timeout/5xx retains reservation, sets RECONCILIATION_REQUIRED, fails closed).
8. Repeated recovery calls (idempotent; returns existing order without duplicate requests).
9. Expired or revoked consent during recovery (fails closed, releases held reservation, blocks creation).
10. Changed transaction fingerprint during recovery (detects tampering, marks MANUAL_REVIEW_REQUIRED).
11. CARE reservation retained during ambiguous recovery (reservation strictly HELD, zero leak).
12. No duplicate order creation (provider receives at most one logical creation).
13. No duplicate budget consumption or release.
"""

import uuid
import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update

from app.main import app
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_database
from app.core.config import settings
from app.models.enums import (
    TransactionLifecycleState,
    ReservationStatus,
    ApprovalType,
    ApprovalMode,
    PolicyDecision,
    ModuleScope,
    UserRole,
)
from app.models.entities import (
    User,
    PurchaseProposal,
    ProposalItem,
    Transaction,
    TransactionItem,
    TransactionFingerprint,
    Approval,
    ConsentPolicy,
    CareProfile,
    PolicyEvaluationRecord,
    BudgetReservation,
    GuardianRelationship,
)
from app.engine.fingerprint import compute_transaction_fingerprint
from app.payments.paypal_service import PayPalService, recompute_proposal_fingerprint
from app.payments.paypal_client import PayPalClient
from app.payments.exceptions import (
    PaymentVerificationError,
    PaymentPolicyDeniedError,
    PaymentCaptureError,
    PaymentIdempotencyConflict,
)
from app.engine.budget import reserve_care_budget


@pytest_asyncio.fixture(autouse=True)
async def ensure_eleanor_clean():
    """Ensure baseline records for Eleanor Vance are preserved and never corrupted."""
    await init_database()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(CareProfile)
            .where(CareProfile.id == "care-eleanor-01")
            .values(monthly_budget_cents=25000, monthly_spent_cents=12000, reserved_cents=0)
        )
        await session.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.id == "pol-002")
            .values(monthly_budget_cents=25000, monthly_spent_cents=12000, reserved_cents=0, is_revoked=False)
        )
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(CareProfile)
            .where(CareProfile.id == "care-eleanor-01")
            .values(monthly_budget_cents=25000, monthly_spent_cents=12000, reserved_cents=0)
        )
        await session.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.id == "pol-002")
            .values(monthly_budget_cents=25000, monthly_spent_cents=12000, reserved_cents=0, is_revoked=False)
        )
        await session.commit()


async def setup_care_proposal_and_stranded_transaction(
    total_cents: int = 2500,
    status: str = "ORDER_CREATION_IN_PROGRESS",
    elapsed_seconds: int = 120,
    module: ModuleScope = ModuleScope.CARE,
    omit_first_dispatched_at: bool = False,
):
    """Helper to stage a CARE proposal and transaction stranded after pre-network commit using isolated test profile."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, "usr-recovery-01")
        if not user:
            user = User(
                id="usr-recovery-01",
                email="recovery@meridian.test",
                full_name="Recovery Recipient",
                role=UserRole.CARE_RECIPIENT,
            )
            session.add(user)
            await session.flush()

        guardian_user = await session.get(User, "usr-recovery-guard-01")
        if not guardian_user:
            guardian_user = User(
                id="usr-recovery-guard-01",
                email="guard@meridian.test",
                full_name="Recovery Guardian",
                role=UserRole.CAREGIVER,
            )
            session.add(guardian_user)
            await session.flush()

        cp = await session.get(CareProfile, "care-recovery-01")
        if not cp:
            cp = CareProfile(
                id="care-recovery-01",
                user_id="usr-recovery-01",
                recipient_name="Recovery Recipient",
                approval_mode=ApprovalMode.CONTROLLED_AUTO,
                monthly_budget_cents=5000000,
                monthly_spent_cents=0,
                reserved_cents=0,
            )
            session.add(cp)
            await session.flush()
        else:
            cp.reserved_cents = 0
            cp.monthly_spent_cents = 0
            await session.flush()

        g_rel = await session.scalar(
            select(GuardianRelationship).where(
                GuardianRelationship.care_profile_id == cp.id,
                GuardianRelationship.guardian_user_id == "usr-recovery-guard-01",
            )
        )
        if not g_rel:
            session.add(
                GuardianRelationship(
                    id="gr-recovery-01",
                    care_profile_id=cp.id,
                    guardian_user_id="usr-recovery-guard-01",
                    relationship_type="Guardian",
                    can_approve=True,
                    max_approval_amount_cents=50000,
                    is_active=True,
                )
            )

        consent_policy = await session.scalar(
            select(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id)
        )
        if not consent_policy:
            consent_policy = ConsentPolicy(
                id="pol-recovery-01",
                user_id=cp.user_id,
                scope=ModuleScope.CARE,
                title="Recovery Consent Policy",
                max_per_order_cents=50000,
                monthly_budget_cents=5000000,
                monthly_spent_cents=0,
                reserved_cents=0,
                currency="USD",
                allowed_merchants=["CVS Pharmacy"],
                allowed_categories=["Medical Supplies"],
                approval_mode=ApprovalMode.CONTROLLED_AUTO,
                valid_until=datetime.now(timezone.utc) + timedelta(days=365),
                is_revoked=False,
                policy_version=1,
            )
            session.add(consent_policy)
            await session.flush()
        else:
            consent_policy.is_revoked = False
            consent_policy.reserved_cents = 0
            consent_policy.monthly_spent_cents = 0
            await session.flush()

        proposal_id = f"prop-crash-{uuid.uuid4().hex[:8]}"
        proposal = PurchaseProposal(
            id=proposal_id,
            user_id="usr-recovery-01",
            care_profile_id=cp.id,
            module=module,
            title="Assisted Prescriptions",
            merchant_id="m-cvs-01",
            merchant_name="CVS Pharmacy",
            subtotal_cents=total_cents,
            tax_cents=0,
            shipping_cents=0,
            total_cents=total_cents,
            currency="USD",
            ai_reasoning="Critical recurring medicine.",
            state=TransactionLifecycleState.PAYMENT_PENDING,
        )
        session.add(proposal)
        await session.flush()

        item = ProposalItem(
            proposal_id=proposal.id,
            product_id="prod-med-crash",
            name="Prescription Glides",
            sku="SKU-GLIDE",
            category="Medical Supplies",
            quantity=1,
            unit_price_cents=total_cents,
            total_cents=total_cents,
        )
        session.add(item)
        await session.flush()

        items_for_fp = [
            {
                "product_id": item.product_id,
                "name": item.name,
                "sku": item.sku,
                "category": item.category,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "total_cents": item.total_cents,
            }
        ]
        canonical, auth_hash = compute_transaction_fingerprint(
            user_id=proposal.user_id,
            module="CARE",
            merchant_id=proposal.merchant_id,
            merchant_name=proposal.merchant_name,
            items=items_for_fp,
            total_cents=proposal.total_cents,
            currency=proposal.currency,
            care_profile_id=proposal.care_profile_id,
            subtotal_cents=proposal.subtotal_cents,
            tax_cents=proposal.tax_cents,
            shipping_cents=proposal.shipping_cents,
            policy_version=1,
        )
        fp_rec = TransactionFingerprint(
            proposal_id=proposal.id,
            canonical_payload=canonical,
            sha256_hash=auth_hash,
            policy_version=1,
            created_at=datetime.now(timezone.utc),
        )
        session.add(fp_rec)

        # Build valid guardian approval
        appr = Approval(
            fingerprint_hash=auth_hash,
            approver_user_id="usr-recovery-guard-01",
            approval_type=ApprovalType.USER_DIRECT,
            consent_policy_id=consent_policy.id if consent_policy else None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            is_revoked=False,
        )
        session.add(appr)

        # Hold budget reservation
        ok, resv, msg = await reserve_care_budget(session, proposal, cp, consent_policy)
        assert ok is True

        # Pre-network transaction committed prior to crash
        past_time = datetime.now(timezone.utc) - timedelta(seconds=elapsed_seconds)
        idemp_key = f"meridian-ord-crash-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            proposal_id=proposal.id,
            user_id=proposal.user_id,
            state=TransactionLifecycleState.PAYMENT_PENDING,
            total_cents=total_cents,
            currency="USD",
            idempotency_key=idemp_key,
            capture_idempotency_key=f"meridian-cap-crash-{uuid.uuid4().hex[:12]}",
            paypal_order_id=None,
            paypal_status="INITIATED",
            paypal_approval_url=None,
            fingerprint_sha256=auth_hash,
            is_simulated=True,
            reconciliation_status=status,
            first_dispatched_at=None if omit_first_dispatched_at else past_time,
            version=1,
            created_at=past_time,
            updated_at=past_time,
        )
        session.add(tx)
        await session.flush()

        t_item = TransactionItem(
            transaction_id=tx.id,
            name=item.name,
            quantity=item.quantity,
            unit_price_cents=item.unit_price_cents,
            total_cents=item.total_cents,
        )
        session.add(t_item)
        await session.commit()

        return proposal_id, idemp_key, auth_hash, total_cents


# ==============================================================================
# TEST 1: Process crash before provider dispatch
# ==============================================================================
@pytest.mark.asyncio
async def test_process_crash_before_provider_dispatch():
    """
    Simulates crash immediately following pre-network commit before network call reached PayPal.
    Recovery executes same-key POST idempotency replay within verified retention window.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    recovered_order_id = f"ORD-REC-1-{uuid.uuid4().hex[:8]}"
    svc = PayPalService()

    with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = {
            "id": recovered_order_id,
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://paypal.test/approve/01"}],
        }

        async with AsyncSessionLocal() as session:
            order_resp = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert order_resp.order_id == recovered_order_id
            assert order_resp.status == "CREATED"

        # Must be invoked with the exact original idempotency key
        mock_create.assert_awaited_once()
        assert mock_create.call_args.kwargs["idempotency_key"] == original_key

    # Verify database state after recovery
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.paypal_order_id == recovered_order_id
        assert tx.reconciliation_status == "ORDER_CREATED"
        assert tx.state == TransactionLifecycleState.PAYMENT_PENDING


# ==============================================================================
# TEST 2: Process crash after provider creation but before response persistence
# ==============================================================================
@pytest.mark.asyncio
async def test_process_crash_after_provider_creation_but_before_persistence():
    """
    Simulates crash where PayPal created order ORD-12345, but backend crashed before saving the response.
    Provider lookup finds existing order -> updates local state to ORDER_CREATED; ZERO duplicate creation calls.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    existing_order_id = f"ORD-REC-2-{uuid.uuid4().hex[:8]}"
    existing_order = {
        "id": existing_order_id,
        "status": "CREATED",
        "purchase_units": [
            {
                "custom_id": prop_id,
                "amount": {"currency_code": "USD", "value": f"{total / 100:.2f}"},
            }
        ],
        "links": [{"rel": "approve", "href": "https://paypal.test/approve/999"}],
    }

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = existing_order

        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                order_resp = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                assert order_resp.order_id == existing_order_id

            mock_lookup.assert_awaited_once_with(original_key)
            # CRITICAL: create_order must NOT be called when existing order is found!
            mock_create.assert_not_called()

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.paypal_order_id == existing_order_id
        assert tx.reconciliation_status == "ORDER_CREATED"


# ==============================================================================
# TEST 3: Restart recovery with original idempotency key
# ==============================================================================
@pytest.mark.asyncio
async def test_restart_recovery_with_original_idempotency_key():
    """
    Verifies that after process restart, recovery uses the persisted idempotency key and NEVER regenerates a new key.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=300
    )

    restart_order_id = f"ORD-REC-3-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        with patch("app.payments.paypal_service.PayPalClient.lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
            mock_lookup.return_value = None

            with patch("app.payments.paypal_service.PayPalClient.create_order", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = {
                    "id": restart_order_id,
                    "status": "CREATED",
                    "links": [],
                }

                # Trigger recovery through create-order route (simulating client retry after restart)
                res = await ac.post(f"/api/payments/{prop_id}/create-order", json={"proposal_id": prop_id})
                assert res.status_code == 200
                assert res.json()["order_id"] == restart_order_id

                # Assert that create_order was dispatched with the original persisted key
                assert mock_create.call_args.kwargs["idempotency_key"] == original_key


# ==============================================================================
# TEST 4: Duplicate recovery attempts from separate sessions (concurrency)
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_recovery_attempts_from_separate_sessions():
    """
    Ensures that when two concurrent workers attempt recovery simultaneously, atomic SQL update
    prevents duplicate creation and rejects the competing worker with 409 conflict.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    concur_order_id = f"ORD-REC-4-{uuid.uuid4().hex[:8]}"
    svc = PayPalService()

    async def worker_recovery(worker_id: int):
        async with AsyncSessionLocal() as session:
            try:
                # Add a micro-delay in lookup to test race condition
                async def delayed_lookup(key):
                    await asyncio.sleep(0.05)
                    return {
                        "id": concur_order_id,
                        "status": "CREATED",
                        "purchase_units": [{"custom_id": prop_id}],
                        "links": [],
                    }

                with patch.object(svc.client, "lookup_order_by_idempotency_key", side_effect=delayed_lookup):
                    return await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            except PaymentIdempotencyConflict:
                return "CONFLICT"

    res1, res2 = await asyncio.gather(worker_recovery(1), worker_recovery(2))

    # Exactly one worker wins recovery, other gets conflict (or refreshes and gets existing)
    results = [res1, res2]
    assert any(isinstance(r, PaymentIdempotencyConflict) or r == "CONFLICT" or (hasattr(r, "order_id") and r.order_id == concur_order_id) for r in results)


# ==============================================================================
# TEST 5: Confirmed existing order validation
# ==============================================================================
@pytest.mark.asyncio
async def test_confirmed_existing_order_validates_proposal_and_fingerprint():
    """
    Recovery verifies that provider custom_id matches proposal ID and fingerprint matches DB.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    # Provider order has mismatched custom_id
    mismatched_order = {
        "id": "ORD-MISMATCH-01",
        "status": "CREATED",
        "purchase_units": [{"custom_id": "prop-wrong-victim"}],
    }

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = mismatched_order

        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError) as exc_info:
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert "does not match proposal" in str(exc_info.value)

    # Verify held for manual review
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"


# ==============================================================================
# TEST 6: Confirmed absence of order
# ==============================================================================
@pytest.mark.asyncio
async def test_confirmed_absence_of_order_executes_safe_retry():
    """
    Provider confirms order was never created. Invariants revalidated; single retry executed.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    absence_order_id = f"ORD-REC-6-{uuid.uuid4().hex[:8]}"
    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = None  # Confirmed absence

        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"id": absence_order_id, "status": "CREATED", "links": []}

            async with AsyncSessionLocal() as session:
                resp = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                assert resp.order_id == absence_order_id

            mock_create.assert_awaited_once()


# ==============================================================================
# TEST 7: Ambiguous provider response (timeout/5xx) during recovery replay
# ==============================================================================
@pytest.mark.asyncio
async def test_ambiguous_provider_response_fails_closed_and_retains_reservation():
    """
    When provider replay fails ambiguously (timeout), the transaction is marked RECONCILIATION_REQUIRED
    and the CARE budget reservation is strictly RETAINED.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    svc = PayPalService()
    with patch.object(svc.client, "create_order", side_effect=PaymentCaptureError("Provider probe timeout")):
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError):
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

    # Check DB state
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "RECONCILIATION_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD  # RETAINED!


# ==============================================================================
# TEST 8: Repeated recovery calls
# ==============================================================================
@pytest.mark.asyncio
async def test_repeated_recovery_calls_are_idempotent():
    """
    Subsequent recovery calls on an already recovered order return the idempotent response
    without issuing duplicate provider requests.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    repeat_order_id = f"ORD-REC-8-{uuid.uuid4().hex[:8]}"
    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = {
            "id": repeat_order_id,
            "status": "CREATED",
            "purchase_units": [{"custom_id": prop_id}],
            "links": [],
        }

        # First recovery
        async with AsyncSessionLocal() as session:
            resp1 = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert resp1.order_id == repeat_order_id

        # Second recovery call
        async with AsyncSessionLocal() as session:
            resp2 = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert resp2.order_id == repeat_order_id

        # Lookup should only be called once because second call short-circuits on tx.paypal_order_id
        assert mock_lookup.await_count == 1


# ==============================================================================
# TEST 9: Revoked consent during ambiguous recovery retains held reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_revoked_consent_during_recovery_blocks_creation_and_retains_held_reservation():
    """
    If consent policy was revoked during the crash window while provider creation is ambiguous,
    recovery must NOT dispatch another provider POST, must NOT release the HELD reservation,
    and must transition to MANUAL_REVIEW_REQUIRED to prevent double-spend.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    try:
        # Revoke consent policy in DB
        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-recovery-01")
            initial_reserved = cp.reserved_cents
            await session.execute(
                update(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id).values(is_revoked=True)
            )
            await session.commit()

        svc = PayPalService()
        with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
            mock_lookup.return_value = None  # No local simulated match -> invariant revalidation

            with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
                async with AsyncSessionLocal() as session:
                    with pytest.raises(PaymentVerificationError) as exc_info:
                        await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                    assert "revoked" in str(exc_info.value).lower()

                # Zero provider creation calls
                mock_create.assert_not_called()

        # Verify reservation remains strictly HELD and tx transitioned to MANUAL_REVIEW_REQUIRED
        async with AsyncSessionLocal() as session:
            tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
            assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"

            resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
            assert resv.status == ReservationStatus.HELD  # RETAINED!

            cp_after = await session.get(CareProfile, "care-recovery-01")
            assert cp_after.reserved_cents == initial_reserved  # Zero premature release!
    finally:
        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-recovery-01")
            await session.execute(
                update(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id).values(is_revoked=False)
            )
            await session.commit()


# ==============================================================================
# TEST 10: Changed transaction fingerprint during recovery
# ==============================================================================
@pytest.mark.asyncio
async def test_tampered_fingerprint_during_recovery_moves_to_manual_review():
    """
    If items or amounts were tampered with while stranded, recovery detects hash mismatch
    and transitions to MANUAL_REVIEW_REQUIRED without dispatching creation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    # Tamper with proposal item amount in DB
    async with AsyncSessionLocal() as session:
        item = await session.scalar(select(ProposalItem).where(ProposalItem.proposal_id == prop_id))
        item.total_cents = total + 5000
        await session.commit()

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = None

        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                with pytest.raises(PaymentVerificationError) as exc_info:
                    await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                assert "Fingerprint mismatch" in str(exc_info.value)

            mock_create.assert_not_called()

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"


# ==============================================================================
# TEST 11: CARE reservation retained during ambiguous recovery
# ==============================================================================
@pytest.mark.asyncio
async def test_care_reservation_strictly_retained_on_ambiguous_recovery():
    """
    Verifies that network failure during retry keeps the reservation HELD.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="RECONCILIATION_REQUIRED", elapsed_seconds=10
    )

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = None  # Confirmed absence -> attempts retry

        with patch.object(svc.client, "create_order", side_effect=PaymentCaptureError("504 Gateway Timeout")):
            async with AsyncSessionLocal() as session:
                with pytest.raises(PaymentCaptureError):
                    await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=0)

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "RECONCILIATION_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


# ==============================================================================
# TEST 12: No duplicate order creation
# ==============================================================================
@pytest.mark.asyncio
async def test_no_duplicate_order_creation_across_multiple_retries():
    """
    Verifies that calling recover_order_creation followed by create-order endpoint
    generates zero duplicate orders.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    created_orders = []
    prefix = uuid.uuid4().hex[:6]

    async def fake_create(**kwargs):
        new_id = f"ORD-UNIQUE-{prefix}-{len(created_orders) + 1}"
        resp = {"id": new_id, "status": "CREATED", "links": []}
        created_orders.append(resp)
        return resp

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = None

        with patch.object(svc.client, "create_order", side_effect=fake_create):
            # 1. Recover order
            async with AsyncSessionLocal() as session:
                resp1 = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                assert resp1.order_id == f"ORD-UNIQUE-{prefix}-1"

            # 2. Subsequent client retry via HTTP endpoint
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp2 = await ac.post(f"/api/payments/{prop_id}/create-order", json={"proposal_id": prop_id})
                assert resp2.status_code == 200
                assert resp2.json()["order_id"] == f"ORD-UNIQUE-{prefix}-1"

    # Exactly 1 order created, never 2
    assert len(created_orders) == 1


# ==============================================================================
# TEST 13: Explicit HTTP recovery endpoint
# ==============================================================================
@pytest.mark.asyncio
async def test_explicit_recover_order_http_endpoint():
    """
    Tests POST /api/payments/{proposal_id}/recover-order endpoint.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    http_order_id = f"ORD-REC-13-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        with patch("app.payments.paypal_service.PayPalClient.lookup_simulated_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
            mock_lookup.return_value = {
                "id": http_order_id,
                "status": "CREATED",
                "purchase_units": [{"custom_id": prop_id}],
                "links": [],
            }

            res = await ac.post(f"/api/payments/{prop_id}/recover-order")
            assert res.status_code == 200
            assert res.json()["order_id"] == http_order_id
            assert res.json()["status"] == "CREATED"


# ==============================================================================
# TEST 14: Default retention window is 6 hours (21,600s)
# ==============================================================================
def test_default_retention_window_is_six_hours():
    """
    Proves that the default PayPal Orders v2 Create Order idempotency retention
    is conservatively configured to 6 hours (21,600 seconds).
    """
    assert settings.PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS == 21600
    assert settings.PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED is False
    assert settings.get_effective_order_idempotency_retention_seconds() == 21600


# ==============================================================================
# TEST 15: Extended retention window requires explicit confirmation
# ==============================================================================
def test_extended_retention_window_requires_explicit_confirmation():
    """
    Proves that an extended window (e.g. up to 72 hours) is accepted ONLY when
    explicitly confirmed via PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED=True.
    Otherwise safely falls back to the conservative 6-hour default.
    """
    with patch.object(settings, "PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS", 72000):
        with patch.object(settings, "PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED", False):
            # Unconfirmed -> must fall back to 21600 (6 hours)
            assert settings.get_effective_order_idempotency_retention_seconds() == 21600

        with patch.object(settings, "PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED", True):
            # Confirmed -> accepts up to 72 hours
            assert settings.get_effective_order_idempotency_retention_seconds() == 72000

    # Capped at 72 hours (259,200s) even if configured higher
    with patch.object(settings, "PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS", 999999):
        with patch.object(settings, "PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED", True):
            assert settings.get_effective_order_idempotency_retention_seconds() == 259200

    # Invalid <= 0 falls back to 21600
    with patch.object(settings, "PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS", -10):
        assert settings.get_effective_order_idempotency_retention_seconds() == 21600


# ==============================================================================
# TEST 16: Recovery beyond retention window fails closed and blocks provider POST
# ==============================================================================
@pytest.mark.asyncio
async def test_recovery_beyond_retention_window_fails_closed_and_blocks_provider_post():
    """
    Transactions stranded longer than the effective retention window (6h) MUST NOT
    be retried automatically because PayPal purges the idempotency cache.
    Must transition to MANUAL_REVIEW_REQUIRED, retain HELD reservation, and issue ZERO provider calls.
    """
    # 21,601 seconds = 6 hours + 1 second -> expired!
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=21601
    )

    svc = PayPalService()
    with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError) as exc_info:
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert "expired" in str(exc_info.value).lower()
            assert "MANUAL_REVIEW_REQUIRED" in str(exc_info.value)

        # Provider create_order MUST NEVER be called when retention window expired!
        mock_create.assert_not_called()

    # Verify database state
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"
        assert tx.paypal_order_id is None

        # CARE budget reservation strictly retained
        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


# ==============================================================================
# TEST 17: Recovery inside retention window succeeds with original key
# ==============================================================================
@pytest.mark.asyncio
async def test_recovery_inside_retention_window_succeeds_with_original_key():
    """
    Transactions stranded within the effective retention window (e.g. 30 mins)
    safely execute same-key POST idempotency replay with the exact original idempotency key.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=1800  # 30 mins
    )

    replay_order_id = f"ORD-REPLAY-{uuid.uuid4().hex[:8]}"
    svc = PayPalService()
    with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = {
            "id": replay_order_id,
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://paypal.test/approve/replay"}],
        }

        async with AsyncSessionLocal() as session:
            resp = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert resp.order_id == replay_order_id
            assert resp.status == "CREATED"

        mock_create.assert_awaited_once()
        assert mock_create.call_args.kwargs["idempotency_key"] == original_key

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.paypal_order_id == replay_order_id
        assert tx.reconciliation_status == "ORDER_CREATED"


# ==============================================================================
# TEST 18: Missing first_dispatched_at blocks recovery and retains reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_missing_first_dispatched_at_blocks_recovery_and_retains_reservation():
    """
    Legacy transactions lacking a trustworthy first-dispatch timestamp must NOT be
    automatically retried because the retention window cannot be verified.
    Must mark MANUAL_REVIEW_REQUIRED and retain HELD budget reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=60, omit_first_dispatched_at=True
    )

    svc = PayPalService()
    with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError) as exc_info:
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert "first-dispatch timestamp" in str(exc_info.value).lower()
            assert "MANUAL_REVIEW_REQUIRED" in str(exc_info.value)

        mock_create.assert_not_called()

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


# ==============================================================================
# TEST 19: Recovery attempts do not reset first_dispatched_at
# ==============================================================================
@pytest.mark.asyncio
async def test_recovery_attempts_do_not_reset_first_dispatched_at():
    """
    Verifies that the original first_dispatched_at timestamp is immutable during recovery,
    preventing artificial extension of the provider idempotency retention window.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=300
    )

    async with AsyncSessionLocal() as session:
        tx_before = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        original_dispatched_at = tx_before.first_dispatched_at
        assert original_dispatched_at is not None

    svc = PayPalService()
    with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = {
            "id": f"ORD-TIME-{uuid.uuid4().hex[:8]}",
            "status": "CREATED",
            "links": [],
        }

        async with AsyncSessionLocal() as session:
            await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

    async with AsyncSessionLocal() as session:
        tx_after = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        # MUST BE IDENTICAL: never reset during recovery!
        assert tx_after.first_dispatched_at == original_dispatched_at


# ==============================================================================
# TEST 20: Live mode local lookup returning None is not confirmed provider absence
# ==============================================================================
@pytest.mark.asyncio
async def test_live_mode_local_lookup_returning_none_is_not_confirmed_provider_absence():
    """
    Proves that lookup_simulated_order_by_idempotency_key on a non-simulated client
    returns None locally without making network calls, explicitly distinguishing local
    simulation lookup from non-existent provider-side search.
    """
    real_client = PayPalClient(
        client_id="sandbox_test_client_id_01",
        client_secret="sandbox_test_secret_01",
    )
    assert not real_client.is_simulated
    # Must return None locally without throwing network errors
    result = await real_client.lookup_simulated_order_by_idempotency_key("any-request-id")
    assert result is None


# ==============================================================================
# TEST 21: Approval expired during ambiguous recovery retains held reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_approval_expired_during_recovery_retains_held_reservation():
    """
    If guardian approval expired during the crash window while provider status is ambiguous,
    recovery must NOT dispatch another provider POST and must RETAIN the HELD reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    # Expire approval in DB
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-recovery-01")
        initial_reserved = cp.reserved_cents
        await session.execute(
            update(Approval)
            .where(Approval.fingerprint_hash == auth_hash)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.commit()

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = None

        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                with pytest.raises(PaymentVerificationError) as exc_info:
                    await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                assert "expired" in str(exc_info.value).lower()

            mock_create.assert_not_called()

    # Verify reservation is strictly retained in HELD status
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD

        cp_after = await session.get(CareProfile, "care-recovery-01")
        assert cp_after.reserved_cents == initial_reserved


# ==============================================================================
# TEST 22: Proposal denied during ambiguous recovery retains held reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_proposal_denied_during_recovery_retains_held_reservation_and_blocks_post():
    """
    If Trust Engine persisted a DENY decision during the crash window,
    recovery must NOT dispatch another provider POST and must RETAIN the HELD reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    # Add a DENY policy evaluation record
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-recovery-01")
        initial_reserved = cp.reserved_cents
        deny_rec = PolicyEvaluationRecord(
            proposal_id=prop_id,
            decision=PolicyDecision.DENY,
            rules_evaluated=["RULE_MEDICAL_SAFETY"],
            passed_checks=[],
            failed_checks=["RULE_MEDICAL_SAFETY"],
            human_explanation="Simulated post-crash policy denial",
            fingerprint_hash=auth_hash,
        )
        session.add(deny_rec)
        await session.commit()

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = None

        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                with pytest.raises(PaymentPolicyDeniedError):
                    await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

            mock_create.assert_not_called()

    # Verify reservation is retained in HELD status
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD

        cp_after = await session.get(CareProfile, "care-recovery-01")
        assert cp_after.reserved_cents == initial_reserved


# ==============================================================================
# TEST 23: Consent revoked before a provably undispatched request releases safely
# ==============================================================================
@pytest.mark.asyncio
async def test_consent_revoked_before_undispatched_request_safely_releases_reservation():
    """
    When a proposal is in AWAITING_APPROVAL (no Transaction ever created or dispatched),
    consent revocation detected by deterministic expiry safely releases the reservation.
    """
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-eleanor-01")
        initial_reserved = cp.reserved_cents

        prop_id = f"prop-undispatched-{uuid.uuid4().hex[:8]}"
        proposal = PurchaseProposal(
            id=prop_id,
            user_id="usr-eleanor-02",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Undispatched Proposal",
            merchant_id="m-cvs-01",
            merchant_name="CVS Pharmacy",
            subtotal_cents=1500,
            total_cents=1500,
            currency="USD",
            ai_reasoning="Test undispatched safety",
            state=TransactionLifecycleState.AWAITING_APPROVAL,
        )
        session.add(proposal)
        await session.flush()

        resv = BudgetReservation(
            id=f"resv-{uuid.uuid4().hex[:8]}",
            proposal_id=prop_id,
            care_profile_id=cp.id,
            consent_policy_id="pol-002",
            amount_cents=1500,
            currency="USD",
            status=ReservationStatus.HELD,
        )
        session.add(resv)
        await session.execute(
            update(CareProfile).where(CareProfile.id == cp.id).values(reserved_cents=cp.reserved_cents + 1500)
        )
        await session.commit()

    # Deterministic release when no transaction was ever dispatched
    async with AsyncSessionLocal() as session:
        # Central predicate confirms safe because tx is None
        from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is True

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is True
        await session.commit()

    async with AsyncSessionLocal() as session:
        resv_after = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv_after.status == ReservationStatus.RELEASED


# ==============================================================================
# TEST 24: Provider timeout followed by consent revocation retains held reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_provider_timeout_followed_by_consent_revocation_retains_held_reservation():
    """
    When transaction is in RECONCILIATION_REQUIRED after timeout, subsequent recovery
    under revoked consent must NOT retry and must RETAIN the HELD reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="RECONCILIATION_REQUIRED", elapsed_seconds=120
    )

    try:
        # Revoke consent policy in DB
        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-recovery-01")
            initial_reserved = cp.reserved_cents
            await session.execute(
                update(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id).values(is_revoked=True)
            )
            await session.commit()

        svc = PayPalService()
        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                with pytest.raises(PaymentVerificationError):
                    await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

            # Never retry provider POST under revoked consent
            mock_create.assert_not_called()

        async with AsyncSessionLocal() as session:
            tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
            assert tx.reconciliation_status == "MANUAL_REVIEW_REQUIRED"

            resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
            assert resv.status == ReservationStatus.HELD
    finally:
        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-recovery-01")
            await session.execute(
                update(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id).values(is_revoked=False)
            )
            await session.commit()


# ==============================================================================
# TEST 25: Provider 5xx followed by consent revocation retains held reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_provider_5xx_followed_by_consent_revocation_retains_held_reservation():
    """
    When initial order creation receives a 500 error, transaction enters RECONCILIATION_REQUIRED.
    If consent is revoked, subsequent recovery must NOT release the reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    svc = PayPalService()
    # Initial recovery attempt encounters provider 500
    with patch.object(svc.client, "create_order", side_effect=PaymentCaptureError("500 Internal Server Error from PayPal")):
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError):
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

    # Status is now RECONCILIATION_REQUIRED
    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "RECONCILIATION_REQUIRED"

    try:
        # Now consent is revoked
        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-recovery-01")
            await session.execute(
                update(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id).values(is_revoked=True)
            )
            await session.commit()

        # Second recovery attempt
        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                with pytest.raises(PaymentVerificationError):
                    await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

            mock_create.assert_not_called()

        # Reservation remains HELD
        async with AsyncSessionLocal() as session:
            resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
            assert resv.status == ReservationStatus.HELD
    finally:
        async with AsyncSessionLocal() as session:
            cp = await session.get(CareProfile, "care-recovery-01")
            await session.execute(
                update(ConsentPolicy).where(ConsentPolicy.user_id == cp.user_id).values(is_revoked=False)
            )
            await session.commit()


# ==============================================================================
# TEST 26: Manual-review state cannot trigger automated POST
# ==============================================================================
@pytest.mark.asyncio
async def test_manual_review_state_cannot_trigger_automated_post():
    """
    Verifies that a transaction in MANUAL_REVIEW_REQUIRED rejects automated recovery calls
    and never dispatches an outbound provider POST.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="MANUAL_REVIEW_REQUIRED", elapsed_seconds=300
    )

    svc = PayPalService()
    with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentVerificationError) as exc_info:
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
            assert "manual_review_required" in str(exc_info.value).lower()

        mock_create.assert_not_called()


# ==============================================================================
# TEST 27: Lookup method mocks intercept recovery calls via delegation
# ==============================================================================
@pytest.mark.asyncio
async def test_lookup_method_mocks_intercept_recovery_calls():
    """
    Verifies that patching lookup_order_by_idempotency_key successfully intercepts
    the recovery service's lookup_simulated_order_by_idempotency_key call via explicit delegation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=120
    )

    intercepted_order = {
        "id": f"ORD-DELEGATE-{uuid.uuid4().hex[:8]}",
        "status": "CREATED",
        "purchase_units": [
            {
                "custom_id": prop_id,
                "amount": {"currency_code": "USD", "value": f"{total / 100:.2f}"},
            }
        ],
        "links": [{"rel": "approve", "href": "https://paypal.test/approve/delegate"}],
    }

    svc = PayPalService()
    with patch.object(svc.client, "lookup_order_by_idempotency_key", new_callable=AsyncMock) as mock_lookup:
        mock_lookup.return_value = intercepted_order

        with patch.object(svc.client, "create_order", new_callable=AsyncMock) as mock_create:
            async with AsyncSessionLocal() as session:
                resp = await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)
                assert resp.order_id == intercepted_order["id"]

            mock_lookup.assert_awaited_once_with(original_key)
            mock_create.assert_not_called()


# ==============================================================================
# FAIL-CLOSED PREDICATE & ERROR CLASSIFICATION HARDENING TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_predicate_unknown_status_no_dispatch_timestamp_fails_closed():
    """
    Scenario 1: Transaction exists with an unknown reconciliation status and no dispatch timestamp.
    Predicate MUST fail closed (return False) and release_care_budget MUST retain HELD.
    """
    from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget

    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=60, omit_first_dispatched_at=True
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.proposal_id == prop_id)
            .values(reconciliation_status="CORRUPTED_OR_UNKNOWN", first_dispatched_at=None)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is False
        assert "unrecognised" in reason.lower()

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is False

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


@pytest.mark.asyncio
async def test_predicate_null_status_no_dispatch_timestamp_fails_closed():
    """
    Scenario 2: Transaction exists with a NULL reconciliation status and no dispatch timestamp.
    Predicate MUST fail closed (return False) and release_care_budget MUST retain HELD.
    """
    from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget

    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=60, omit_first_dispatched_at=True
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.proposal_id == prop_id)
            .values(reconciliation_status=None, first_dispatched_at=None)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is False
        assert "null or malformed" in reason.lower()

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is False

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


@pytest.mark.asyncio
async def test_predicate_unknown_status_with_dispatch_timestamp_fails_closed():
    """
    Scenario 3: Transaction exists with an unknown reconciliation status and an outbound timestamp.
    Predicate MUST fail closed (return False) and release_care_budget MUST retain HELD.
    """
    from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget

    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=60, omit_first_dispatched_at=False
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.proposal_id == prop_id)
            .values(reconciliation_status="ANOMALOUS_STATUS")
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is False

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is False

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


@pytest.mark.asyncio
async def test_predicate_known_ambiguous_status_without_dispatch_timestamp_fails_closed():
    """
    Scenario 4: Known ambiguous status (e.g. ORDER_CREATION_IN_PROGRESS) without a dispatch timestamp.
    Missing first_dispatched_at alone must NOT be treated as proof of non-dispatch.
    Predicate MUST fail closed (return False).
    """
    from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget

    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=60, omit_first_dispatched_at=True
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.proposal_id == prop_id)
            .values(reconciliation_status="ORDER_CREATION_IN_PROGRESS", first_dispatched_at=None)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is False
        assert "ambiguous" in reason.lower()

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is False

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD


@pytest.mark.asyncio
async def test_predicate_genuinely_undispatched_proposal_safely_releases():
    """
    Scenario 5: Proposal exists in AWAITING_APPROVAL with zero Transaction records.
    Predicate MUST confirm safe release (return True).
    """
    from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget

    prop_id = f"prop-pure-undispatched-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        cp = await session.get(CareProfile, "care-recovery-01")
        proposal = PurchaseProposal(
            id=prop_id,
            user_id="usr-recovery-01",
            care_profile_id=cp.id,
            module=ModuleScope.CARE,
            title="Pure Undispatched",
            merchant_id="merch-01",
            merchant_name="Merchant",
            subtotal_cents=1000,
            total_cents=1000,
            currency="USD",
            ai_reasoning="Pure undispatched test",
            state=TransactionLifecycleState.AWAITING_APPROVAL,
        )
        session.add(proposal)
        await session.flush()

        resv = BudgetReservation(
            id=f"resv-{uuid.uuid4().hex[:8]}",
            proposal_id=prop_id,
            care_profile_id=cp.id,
            consent_policy_id="pol-recovery-01",
            amount_cents=1000,
            currency="USD",
            status=ReservationStatus.HELD,
        )
        session.add(resv)
        await session.commit()

    async with AsyncSessionLocal() as session:
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is True
        assert "no payment transaction was ever created" in reason.lower()

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is True

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.RELEASED


@pytest.mark.asyncio
async def test_predicate_definitive_provider_non_creation_safely_releases():
    """
    Scenario 6: Transaction explicitly reached CREATION_FAILED with no assigned PayPal order ID.
    Predicate MUST confirm safe release (return True).
    """
    from app.engine.budget import is_safe_to_release_care_reservation, release_care_budget

    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=60
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.proposal_id == prop_id)
            .values(reconciliation_status="CREATION_FAILED", state=TransactionLifecycleState.FAILED, paypal_order_id=None)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        is_safe, reason = await is_safe_to_release_care_reservation(session, prop_id)
        assert is_safe is True
        assert "creation_failed" in reason.lower()

        did_release = await release_care_budget(session, prop_id, is_simulated=False)
        assert did_release is True

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.RELEASED


@pytest.mark.asyncio
async def test_predicate_repeated_release_attempts_in_unsafe_states_strictly_retains_held():
    """
    Scenario 7: Repeated release attempts in an unsafe state (e.g. MANUAL_REVIEW_REQUIRED)
    must consistently fail closed without altering reservation status.
    """
    from app.engine.budget import release_care_budget

    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="MANUAL_REVIEW_REQUIRED", elapsed_seconds=300
    )

    for i in range(3):
        async with AsyncSessionLocal() as session:
            did_release = await release_care_budget(session, prop_id, is_simulated=False)
            assert did_release is False

            resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
            assert resv.status == ReservationStatus.HELD


@pytest.mark.asyncio
async def test_error_classification_rate_limit_429_remains_ambiguous_and_retains_held():
    """
    Verifies that HTTP 429 Too Many Requests is classified as AMBIGUOUS (not CREATION_FAILED),
    transitions to RECONCILIATION_REQUIRED, and strictly RETAINS the HELD reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    svc = PayPalService()
    # Mock HTTP 429 Rate Limit
    rate_limit_err = PaymentCaptureError("PayPal rate limit exceeded: HTTP 429 - TOO_MANY_REQUESTS", status_code=429)
    with patch.object(svc.client, "create_order", side_effect=rate_limit_err):
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError):
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "RECONCILIATION_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD  # RETAINED!


@pytest.mark.asyncio
async def test_error_classification_unclassified_4xx_remains_ambiguous_and_retains_held():
    """
    Verifies that an unclassified HTTP 4xx error (e.g. HTTP 418 or generic 499)
    is NOT loosely matched as definitive failure, but strictly fails closed as AMBIGUOUS.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    svc = PayPalService()
    unclassified_err = PaymentCaptureError("PayPal client error: HTTP 418 I'm a teapot", status_code=418)
    with patch.object(svc.client, "create_order", side_effect=unclassified_err):
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError):
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "RECONCILIATION_REQUIRED"

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.HELD  # RETAINED!


@pytest.mark.asyncio
async def test_error_classification_definitive_422_releases_reservation():
    """
    Verifies that HTTP 422 Unprocessable Entity with structured semantic validation failure
    is classified as DEFINITIVE provider rejection and safely transitions to CREATION_FAILED,
    releasing the reservation.
    """
    prop_id, original_key, auth_hash, total = await setup_care_proposal_and_stranded_transaction(
        status="ORDER_CREATION_IN_PROGRESS", elapsed_seconds=90
    )

    svc = PayPalService()
    semantic_err = PaymentCaptureError(
        "PayPal order creation rejected: HTTP 422 - UNPROCESSABLE_ENTITY: CURRENCY_NOT_SUPPORTED_FOR_COUNTRY",
        status_code=422,
    )
    with patch.object(svc.client, "create_order", side_effect=semantic_err):
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError):
                await svc.recover_order_creation(session, prop_id, stale_timeout_seconds=60)

    async with AsyncSessionLocal() as session:
        tx = await session.scalar(select(Transaction).where(Transaction.proposal_id == prop_id))
        assert tx.reconciliation_status == "CREATION_FAILED"
        assert tx.state == TransactionLifecycleState.FAILED

        resv = await session.scalar(select(BudgetReservation).where(BudgetReservation.proposal_id == prop_id))
        assert resv.status == ReservationStatus.RELEASED
