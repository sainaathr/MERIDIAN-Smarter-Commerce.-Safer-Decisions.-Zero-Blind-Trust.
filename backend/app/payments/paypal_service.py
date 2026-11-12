import uuid
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update, or_, and_
from sqlalchemy.orm import selectinload

from app.models.enums import (
    TransactionLifecycleState,
    AuditEventType,
    SeverityLevel,
    ApprovalMode,
    PolicyDecision,
    ReservationStatus,
    ModuleScope,
)
from app.models.entities import (
    PurchaseProposal,
    ProposalItem,
    Transaction,
    TransactionItem,
    TransactionFingerprint,
    Approval,
    ConsentPolicy,
    CareProfile,
    PolicyEvaluationRecord,
    AuditEvent,
    BudgetReservation,
    BusinessProfile,
    Invoice,
)
from app.engine.fingerprint import compute_transaction_fingerprint
from app.engine.lifecycle import validate_transition, IllegalStateTransitionError
from app.engine.approval import verify_approval_integrity
from app.engine.budget import consume_care_budget, release_care_budget
from app.core.config import settings
from app.payments.exceptions import (
    PaymentVerificationError,
    PaymentPolicyDeniedError,
    PaymentCaptureError,
    PaymentIdempotencyConflict,
    ConfirmedUncapturedOrderError,
    MonetaryValidationError,
)
from app.payments.paypal_client import (
    PayPalClient,
    parse_monetary_to_minor_units,
    paypal_amount_to_cents,
)
from app.payments.schemas import (
    PayPalOrderResponse,
    PayPalCaptureResponse,
    PaymentDetailResponse,
)

logger = logging.getLogger(__name__)

def to_str(val: Any) -> str:
    if val is None:
        return ""
    return val.value if hasattr(val, "value") else str(val)

def recompute_proposal_fingerprint(proposal: PurchaseProposal) -> tuple[str, str]:
    """Deterministically recalculates SHA-256 fingerprint from authoritative database items."""
    items_for_fp = [
        {
            "product_id": it.product_id,
            "name": it.name,
            "sku": it.sku,
            "category": it.category,
            "quantity": it.quantity,
            "unit_price_cents": it.unit_price_cents,
            "total_cents": it.total_cents,
        }
        for it in proposal.items
    ]
    policy_ver = proposal.fingerprint.policy_version if proposal.fingerprint else 1
    return compute_transaction_fingerprint(
        user_id=proposal.user_id,
        module=to_str(proposal.module),
        merchant_id=proposal.merchant_id,
        merchant_name=proposal.merchant_name,
        items=items_for_fp,
        total_cents=proposal.total_cents,
        currency=proposal.currency,
        care_profile_id=proposal.care_profile_id,
        subtotal_cents=proposal.subtotal_cents,
        tax_cents=proposal.tax_cents,
        shipping_cents=proposal.shipping_cents,
        policy_version=policy_ver,
    )


def classify_order_creation_error(order_err: Exception) -> tuple[bool, str]:
    """
    Classifies an error encountered during PayPal order creation.
    Strictly distinguishes definitive provider non-creation from ambiguous or unknown outcomes.

    Definitive Non-Creation (returns True):
    - Provider explicitly returned HTTP 400 Bad Request (syntax/validation rejection)
    - Provider explicitly returned HTTP 422 Unprocessable Entity (semantic rejection)
    - Provider explicitly returned HTTP 401 Unauthorized / HTTP 403 Forbidden (authorization rejection)

    Ambiguous Outcomes (returns False):
    - Connection resets, timeouts, DNS/socket failures
    - HTTP 5xx (500, 502, 503, 504)
    - HTTP 429 (Rate Limit / Throttling)
    - HTTP 408 (Request Timeout)
    - Unclassified 4xx or unexpected exceptions

    Returns:
        (is_definitive_rejection: bool, classification_reason: str)
    """
    err_msg = str(order_err).lower()
    status_code = getattr(order_err, "status_code", None)

    # 1. Check for explicit ambiguous markers (timeouts, network, rate limits, 5xx)
    is_ambiguous = (
        "timeout" in err_msg
        or "network" in err_msg
        or "server error" in err_msg
        or "connection" in err_msg
        or "50" in err_msg
        or "429" in err_msg
        or "rate limit" in err_msg
        or "408" in err_msg
        or status_code in {408, 429, 500, 502, 503, 504}
    )
    if is_ambiguous:
        return False, f"Ambiguous outcome (network, timeout, rate-limit, or 5xx): {order_err}"

    # 2. Check for explicit definitive non-creation markers (400, 401, 403, 422)
    is_definitive_code = status_code in {400, 401, 403, 422}
    is_definitive_msg = any(
        marker in err_msg
        for marker in (
            "http 400", "400 bad request",
            "http 401", "401 unauthorized",
            "http 403", "403 forbidden",
            "http 422", "422 unprocessable",
            "authorization rejected",
            "invalid_request", "unprocessable_entity",
        )
    )

    if is_definitive_code or is_definitive_msg:
        return True, f"Definitive provider rejection: {order_err}"

    # 3. Fail closed on all unknown or unclassified errors
    return False, f"Unclassified error (treated as ambiguous): {order_err}"


class PayPalService:
    """
    Coordinates PayPal Sandbox order creation and payment capture strictly within
    the Universal Trust Engine permission and cryptographic boundary.
    """

    def __init__(self, client: Optional[PayPalClient] = None):
        self.client = client or PayPalClient()
        self._local_locks: Dict[str, asyncio.Lock] = {}

    async def create_order_for_proposal(
        self,
        db: AsyncSession,
        proposal_id: str,
        idempotency_key: Optional[str] = None,
        current_user_id: Optional[str] = None,
    ) -> PayPalOrderResponse:
        """
        Gated PayPal order creation.
        Verifies:
        1. Proposal exists.
        2. Cryptographic fingerprint matches stored hash.
        3. Proposal is in APPROVED state.
        4. Valid human or policy approval exists, unrevoked, unexpired.
        5. Moves lifecycle to PAYMENT_PENDING.
        6. Persists Transaction record and emits AUDIT_PAYPAL_ORDER_CREATED.
        """
        # Fetch proposal with relationships
        query = (
            select(PurchaseProposal)
            .where(PurchaseProposal.id == proposal_id)
            .options(
                selectinload(PurchaseProposal.items),
                selectinload(PurchaseProposal.fingerprint),
                selectinload(PurchaseProposal.transactions),
            )
        )
        result = await db.execute(query)
        proposal = result.scalar_one_or_none()

        if not proposal:
            raise PaymentVerificationError(f"Purchase proposal '{proposal_id}' not found.")

        if not proposal.fingerprint:
            raise PaymentVerificationError("Proposal lacks registered cryptographic fingerprint.")

        # Recompute authoritative fingerprint
        _, authoritative_hash = recompute_proposal_fingerprint(proposal)
        if proposal.fingerprint.sha256_hash != authoritative_hash:
            raise PaymentVerificationError(
                f"Fingerprint integrity failure: Stored {proposal.fingerprint.sha256_hash} "
                f"does not match authoritative database items {authoritative_hash}."
            )

        # Check existing transaction for idempotency
        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        existing_tx = tx_res.scalars().first()

        if existing_tx and existing_tx.paypal_order_id:
            if existing_tx.state in (TransactionLifecycleState.PAYMENT_PENDING, TransactionLifecycleState.PAID):
                # Return idempotent response
                return PayPalOrderResponse(
                    order_id=existing_tx.paypal_order_id,
                    status=existing_tx.paypal_status or "CREATED",
                    intent="CAPTURE",
                    approval_url=existing_tx.paypal_approval_url,
                    total_cents=existing_tx.total_cents,
                    currency=existing_tx.currency,
                    proposal_id=proposal.id,
                    fingerprint_sha256=existing_tx.fingerprint_sha256,
                    is_simulated=existing_tx.is_simulated,
                    created_at=existing_tx.created_at,
                )

        if existing_tx and existing_tx.reconciliation_status in (
            "ORDER_CREATION_IN_PROGRESS",
            "RECONCILIATION_REQUIRED",
            "ORDER_CREATION_RECOVERING",
        ):
            now_check_utc = datetime.now(timezone.utc)
            tx_updated_utc = existing_tx.updated_at
            if tx_updated_utc and tx_updated_utc.tzinfo is None:
                tx_updated_utc = tx_updated_utc.replace(tzinfo=timezone.utc)
            elapsed_seconds = (now_check_utc - tx_updated_utc).total_seconds() if tx_updated_utc else 999

            # If transaction is stranded/stale (elapsed >= 60s) or explicitly marked RECONCILIATION_REQUIRED,
            # execute deterministic fail-closed crash recovery using the persisted idempotency key!
            if elapsed_seconds >= 60 or existing_tx.reconciliation_status == "RECONCILIATION_REQUIRED":
                return await self.recover_order_creation(
                    db=db,
                    proposal_id=proposal_id,
                    stale_timeout_seconds=0,
                )
            else:
                raise PaymentIdempotencyConflict(
                    "Order creation is actively in progress for this proposal. Concurrent creation rejected."
                )

        # Invariant: Disallow order creation if proposal was rejected or has a DENY evaluation
        current_state = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )

        if current_state == TransactionLifecycleState.REJECTED:
            raise PaymentPolicyDeniedError("Cannot create payment order: Proposal has been REJECTED by policy.")

        deny_eval_query = (
            select(PolicyEvaluationRecord)
            .where(
                PolicyEvaluationRecord.proposal_id == proposal_id,
                PolicyEvaluationRecord.decision == PolicyDecision.DENY,
            )
        )
        deny_eval_res = await db.execute(deny_eval_query)
        if deny_eval_res.scalars().first():
            raise PaymentPolicyDeniedError("Cannot create payment order: Proposal has a Trust Engine 'DENY' policy decision.")

        # Invariant: Must strictly be in APPROVED state to create a new order
        if current_state != TransactionLifecycleState.APPROVED:
            raise PaymentVerificationError(
                f"Cannot create payment order for proposal in '{current_state.value}' state. "
                "Explicit approval by authorized human or verified policy is required."
            )

        # Check existing approval record bound to fingerprint
        approval_query = (
            select(Approval)
            .where(Approval.fingerprint_hash == authoritative_hash)
            .order_by(desc(Approval.created_at))
        )
        app_res = await db.execute(approval_query)
        approval = app_res.scalars().first()

        approval_dict = None
        if approval:
            approval_dict = {
                "id": approval.id,
                "approver_user_id": approval.approver_user_id,
                "approval_type": approval.approval_type,
                "fingerprint_hash": approval.fingerprint_hash,
                "expires_at": approval.expires_at,
                "is_revoked": approval.is_revoked,
                "revoked_reason": approval.revoked_reason,
            }

        # Check policy revocation if linked
        policy_dict = None
        if approval and approval.consent_policy_id:
            pol_res = await db.execute(
                select(ConsentPolicy).where(ConsentPolicy.id == approval.consent_policy_id)
            )
            pol = pol_res.scalar_one_or_none()
            if pol:
                policy_dict = {
                    "is_revoked": pol.is_revoked,
                    "expires_at": getattr(pol, "valid_until", getattr(pol, "expires_at", None)),
                }

        is_care_proposal = (to_str(proposal.module) == "CARE" or bool(getattr(proposal, "care_profile_id", None)))
        verif_result = verify_approval_integrity(
            approval=approval_dict,
            expected_fingerprint_hash=authoritative_hash,
            policy=policy_dict,
            expected_user_id=proposal.user_id,
            current_user_id=current_user_id,
            proposal_id=proposal_id,
            checkout_attempt_id=f"chk-{proposal_id[:8]}-{uuid.uuid4().hex[:8]}",
            is_care=is_care_proposal,
        )

        if not verif_result.is_valid:
            # If the approval was revoked or marked suspicious, transition the proposal state cleanly so it does not remain falsely 'APPROVED'
            if verif_result.reason_code in ("SUSPICIOUS_ACTIVITY_CONFIRMED", "APPROVAL_EXPLICITLY_REVOKED", "CONSENT_POLICY_REVOKED"):
                proposal.state = TransactionLifecycleState.REJECTED
                db.add(
                    AuditEvent(
                        timestamp=datetime.now(timezone.utc),
                        event_type=AuditEventType.SECURITY_BLOCK if verif_result.reason_code == "SUSPICIOUS_ACTIVITY_CONFIRMED" else AuditEventType.APPROVAL_REVOKED,
                        actor=current_user_id or proposal.user_id,
                        role="SECURITY_KERNEL",
                        module=to_str(proposal.module),
                        summary=f"Checkout blocked & proposal invalidated: {verif_result.reason}",
                        entity_id=proposal.id,
                        fingerprint=authoritative_hash,
                        previous_state="APPROVED",
                        new_state="REJECTED",
                        details={"reason_code": verif_result.reason_code, "reason": verif_result.reason},
                        severity=SeverityLevel.ALERT if verif_result.reason_code == "SUSPICIOUS_ACTIVITY_CONFIRMED" else SeverityLevel.WARNING,
                    )
                )
                await db.commit()
            raise PaymentVerificationError(f"Approval integrity failure: {verif_result.reason}")

        # Fresh Gate A affordability recheck for BUSINESS proposals immediately prior to provider dispatch
        if proposal.module == ModuleScope.BUSINESS or to_str(proposal.module) == "BUSINESS":
            from app.engine.business import check_business_affordability

            bp_res = await db.execute(
                select(BusinessProfile).where(BusinessProfile.user_id == proposal.user_id)
            )
            biz_profile = bp_res.scalar_one_or_none()
            if not biz_profile:
                raise PaymentPolicyDeniedError(
                    f"Cannot create payment order: Business profile for user '{proposal.user_id}' not found."
                )

            inv_res = await db.execute(
                select(Invoice).where(Invoice.business_profile_id == biz_profile.id)
            )
            current_invoices = inv_res.scalars().all()

            affordability = check_business_affordability(
                business_profile=biz_profile,
                invoices=current_invoices,
                proposed_amount_cents=proposal.total_cents,
            )

            if not affordability.is_affordable:
                now_block_utc = datetime.now(timezone.utc)
                db.add(
                    AuditEvent(
                        timestamp=now_block_utc,
                        event_type=AuditEventType.SECURITY_BLOCK,
                        actor=proposal.user_id,
                        role="TRUST_ENGINE",
                        module=to_str(proposal.module),
                        summary=(
                            f"Dispatch Affordability Block: Payment dispatch rejected for Business Proposal #{proposal.id}. "
                            f"{affordability.reason}"
                        ),
                        entity_id=proposal.id,
                        fingerprint=authoritative_hash,
                        previous_state=to_str(current_state),
                        new_state=to_str(current_state),
                        decision="DENY",
                        details={
                            "total_cents": proposal.total_cents,
                            "affordability": affordability.model_dump(),
                            "block_stage": "PRE_DISPATCH_GATE_A_RECHECK",
                        },
                        severity=SeverityLevel.ALERT,
                    )
                )
                await db.commit()
                raise PaymentPolicyDeniedError(
                    f"Payment dispatch rejected by Gate A cashflow engine: {affordability.reason}"
                )

        # Safe transition to PAYMENT_PENDING
        try:
            validate_transition(current_state, TransactionLifecycleState.PAYMENT_PENDING)
            proposal.state = TransactionLifecycleState.PAYMENT_PENDING
        except IllegalStateTransitionError as exc:
            raise PaymentVerificationError(str(exc))

        # Defect 3: Restructure order creation into durable pre-network and post-network sequence.
        # Stable idempotency keys
        order_key = idempotency_key or (existing_tx.idempotency_key if existing_tx else None) or f"meridian-ord-{proposal.id[:8]}-{uuid.uuid4().hex[:8]}"
        cap_idemp_key = (existing_tx.capture_idempotency_key if existing_tx else None) or f"meridian-cap-{proposal.id[:8]}-{uuid.uuid4().hex[:8]}"

        now_utc = datetime.now(timezone.utc)
        items_dict = [
            {
                "name": it.name,
                "quantity": it.quantity,
                "unit_price_cents": it.unit_price_cents,
                "total_cents": it.total_cents,
            }
            for it in proposal.items
        ]

        # 1. Pre-network durable intent persistence
        if not existing_tx:
            existing_tx = Transaction(
                proposal_id=proposal.id,
                user_id=proposal.user_id,
                state=TransactionLifecycleState.PAYMENT_PENDING,
                total_cents=proposal.total_cents,
                currency=proposal.currency,
                idempotency_key=order_key,
                capture_idempotency_key=cap_idemp_key,
                paypal_order_id=None,
                paypal_status="INITIATED",
                paypal_approval_url=None,
                fingerprint_sha256=authoritative_hash,
                is_simulated=self.client.is_simulated,
                reconciliation_status="ORDER_CREATION_IN_PROGRESS",
                first_dispatched_at=now_utc,
                version=1,
            )
            db.add(existing_tx)
            await db.flush()

            # Add transaction line items
            for it in proposal.items:
                t_item = TransactionItem(
                    transaction_id=existing_tx.id,
                    name=it.name,
                    quantity=it.quantity,
                    unit_price_cents=it.unit_price_cents,
                    total_cents=it.total_cents,
                )
                db.add(t_item)
        else:
            existing_tx.state = TransactionLifecycleState.PAYMENT_PENDING
            existing_tx.reconciliation_status = "ORDER_CREATION_IN_PROGRESS"
            existing_tx.paypal_status = "INITIATED"
            existing_tx.idempotency_key = order_key
            if not existing_tx.first_dispatched_at:
                existing_tx.first_dispatched_at = now_utc
            existing_tx.updated_at = now_utc

        # CRITICAL: Commit database transaction BEFORE making external PayPal network request.
        # Guarantees no checked-out DB connection or open transaction is held during external I/O.
        await db.commit()

        # 2. Outbound PayPal Network I/O (no open database transaction)
        order_resp = None
        order_err = None
        try:
            order_resp = await self.client.create_order(
                total_cents=proposal.total_cents,
                currency=proposal.currency,
                items=items_dict,
                custom_id=proposal.id,
                idempotency_key=order_key,
                subtotal_cents=proposal.subtotal_cents,
                tax_cents=proposal.tax_cents,
                shipping_cents=proposal.shipping_cents,
            )
        except Exception as exc:
            order_err = exc

        # 3. Post-network processing in fresh database transaction
        now_post_utc = datetime.now(timezone.utc)
        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        tx = tx_res.scalars().first()

        prop_query = select(PurchaseProposal).where(PurchaseProposal.id == proposal_id)
        prop_res = await db.execute(prop_query)
        prop = prop_res.scalars().first()

        if order_err:
            err_msg = str(order_err)
            is_definitive, reason = classify_order_creation_error(order_err)
            if not is_definitive:
                # Ambiguous outcome -> retain reservation, mark reconciliation required
                if tx:
                    tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                    tx.updated_at = now_post_utc
                db.add(
                    AuditEvent(
                        timestamp=now_post_utc,
                        event_type=AuditEventType.STATE_TRANSITION,
                        actor=proposal.user_id,
                        role="PAYPAL_GATEWAY",
                        module=to_str(proposal.module),
                        summary=f"PayPal order creation outcome ambiguous for Proposal #{proposal_id}. Retaining reservation, reconciliation required: {reason}",
                        entity_id=proposal_id,
                        previous_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
                        new_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
                        details={"error": err_msg, "classification": reason, "reconciliation_status": "RECONCILIATION_REQUIRED"},
                        severity=SeverityLevel.WARNING,
                    )
                )
                await db.commit()
                raise order_err
            else:
                # Definite failure confirming no order was created on PayPal -> release held reservation
                if tx:
                    tx.state = TransactionLifecycleState.FAILED
                    tx.paypal_status = "FAILED"
                    tx.reconciliation_status = "CREATION_FAILED"
                    tx.updated_at = now_post_utc
                if prop:
                    prop.state = TransactionLifecycleState.FAILED
                    prop.updated_at = now_post_utc

                await release_care_budget(db, proposal_id, is_simulated=False)

                db.add(
                    AuditEvent(
                        timestamp=now_post_utc,
                        event_type=AuditEventType.STATE_TRANSITION,
                        actor=proposal.user_id,
                        role="PAYPAL_GATEWAY",
                        module=to_str(proposal.module),
                        summary=f"PayPal order creation definitely failed for Proposal #{proposal_id}. Released reservation: {err_msg}",
                        entity_id=proposal_id,
                        previous_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
                        new_state=to_str(TransactionLifecycleState.FAILED),
                        details={"error": err_msg},
                        severity=SeverityLevel.ALERT,
                    )
                )
                await db.commit()
                raise order_err

        # Order creation succeeded
        order_id = order_resp.get("id")
        if not order_id:
            if tx:
                tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                tx.updated_at = now_post_utc
            await db.commit()
            raise PaymentVerificationError("PayPal returned order response missing order ID.")

        order_status = order_resp.get("status", "CREATED")
        approval_url = None
        for link in order_resp.get("links", []):
            if link.get("rel") == "approve":
                approval_url = link.get("href")
                break

        if tx:
            tx.paypal_order_id = order_id
            tx.paypal_status = order_status
            tx.paypal_approval_url = approval_url
            tx.reconciliation_status = "ORDER_CREATED"
            tx.updated_at = now_post_utc

        audit = AuditEvent(
            timestamp=now_post_utc,
            event_type=AuditEventType.PAYPAL_ORDER_CREATED,
            actor=proposal.user_id,
            role="PAYPAL_GATEWAY",
            module=to_str(proposal.module),
            summary=f"Created PayPal Sandbox Order #{order_id} for Proposal #{proposal.id} bound to fingerprint {authoritative_hash[:16]}...",
            entity_id=proposal.id,
            fingerprint=authoritative_hash,
            previous_state=to_str(current_state),
            new_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
            details={
                "paypal_order_id": order_id,
                "order_status": order_status,
                "total_cents": proposal.total_cents,
                "currency": proposal.currency,
                "is_simulated": self.client.is_simulated,
            },
            severity=SeverityLevel.INFO,
        )
        db.add(audit)

        await db.commit()
        if tx:
            await db.refresh(tx)

        return PayPalOrderResponse(
            order_id=order_id,
            status=order_status,
            intent="CAPTURE",
            approval_url=approval_url,
            total_cents=proposal.total_cents,
            currency=proposal.currency,
            proposal_id=proposal.id,
            fingerprint_sha256=authoritative_hash,
            is_simulated=self.client.is_simulated,
            created_at=now_post_utc,
        )

    async def recover_order_creation(
        self,
        db: AsyncSession,
        proposal_id: str,
        stale_timeout_seconds: int = 60,
    ) -> PayPalOrderResponse:
        """
        Deterministic crash and timeout recovery for stranded PayPal order creation attempts.
        Enforces fail-closed, reconciliation-first recovery:
        1. Employs durable, persisted idempotency key (NEVER generates a new key on retry).
        2. Atomic DB concurrency claim (ORDER_CREATION_RECOVERING) prevents racing workers.
        3. Probes provider to determine if order was already created on PayPal Sandbox.
        4. If existing order is positively identified: validates custom_id, amount, currency,
           and cryptographic fingerprint, persisting provider order ID without duplicate creation.
        5. If provider positively confirms absence of order: revalidates proposal state, fingerprint,
           approval validity, consent policy, and held CARE budget reservation before dispatching
           a single retry with the original idempotency key.
        6. If provider response is ambiguous (5xx or timeout): retains held budget reservation,
           marks RECONCILIATION_REQUIRED, and fails closed without creating duplicate orders.
        7. If revalidation detects revoked consent, expired approval, or fingerprint mismatch:
           safely blocks execution, releases held budget reservation, and marks RECOVERY_BLOCKED.
        """
        now_utc = datetime.now(timezone.utc)

        # 1. Load proposal with full relationships
        query = (
            select(PurchaseProposal)
            .where(PurchaseProposal.id == proposal_id)
            .options(
                selectinload(PurchaseProposal.items),
                selectinload(PurchaseProposal.fingerprint),
                selectinload(PurchaseProposal.transactions),
            )
        )
        res = await db.execute(query)
        proposal = res.scalar_one_or_none()
        if not proposal:
            raise PaymentVerificationError(f"Purchase proposal '{proposal_id}' not found.")

        # Load existing transaction
        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        existing_tx = tx_res.scalars().first()
        if not existing_tx:
            raise PaymentVerificationError(f"No payment transaction found to recover for proposal '{proposal_id}'.")

        # 2. Check if order was already completed
        if existing_tx.paypal_order_id:
            if existing_tx.state in (TransactionLifecycleState.PAYMENT_PENDING, TransactionLifecycleState.PAID):
                return PayPalOrderResponse(
                    order_id=existing_tx.paypal_order_id,
                    status=existing_tx.paypal_status or "CREATED",
                    intent="CAPTURE",
                    approval_url=existing_tx.paypal_approval_url,
                    total_cents=existing_tx.total_cents,
                    currency=existing_tx.currency,
                    proposal_id=proposal.id,
                    fingerprint_sha256=existing_tx.fingerprint_sha256,
                    is_simulated=existing_tx.is_simulated,
                    created_at=existing_tx.created_at,
                )

        if existing_tx.state == TransactionLifecycleState.FAILED and existing_tx.reconciliation_status == "CREATION_FAILED":
            raise PaymentVerificationError(f"Transaction for proposal '{proposal_id}' has permanently failed.")

        # 3. Atomic concurrency claim for recovery
        stale_cutoff = now_utc - timedelta(seconds=stale_timeout_seconds)
        claim_conditions = [
            Transaction.reconciliation_status == "RECONCILIATION_REQUIRED",
        ]
        if stale_timeout_seconds > 0:
            claim_conditions.extend([
                and_(
                    Transaction.reconciliation_status == "ORDER_CREATION_IN_PROGRESS",
                    Transaction.updated_at <= stale_cutoff,
                ),
                and_(
                    Transaction.reconciliation_status == "ORDER_CREATION_RECOVERING",
                    Transaction.updated_at <= stale_cutoff,
                ),
            ])
        else:
            claim_conditions.extend([
                Transaction.reconciliation_status == "ORDER_CREATION_IN_PROGRESS",
                Transaction.reconciliation_status == "ORDER_CREATION_RECOVERING",
            ])

        claim_stmt = (
            update(Transaction)
            .where(
                Transaction.id == existing_tx.id,
                or_(*claim_conditions),
            )
            .values(
                reconciliation_status="ORDER_CREATION_RECOVERING",
                version=Transaction.version + 1,
                updated_at=now_utc,
            )
            .execution_options(synchronize_session=False)
        )
        claim_res = await db.execute(claim_stmt)
        await db.commit()

        if claim_res.rowcount == 0:
            # Another concurrent worker claimed or completed it
            await db.refresh(existing_tx)
            if existing_tx.paypal_order_id:
                return PayPalOrderResponse(
                    order_id=existing_tx.paypal_order_id,
                    status=existing_tx.paypal_status or "CREATED",
                    intent="CAPTURE",
                    approval_url=existing_tx.paypal_approval_url,
                    total_cents=existing_tx.total_cents,
                    currency=existing_tx.currency,
                    proposal_id=proposal.id,
                    fingerprint_sha256=existing_tx.fingerprint_sha256,
                    is_simulated=existing_tx.is_simulated,
                    created_at=existing_tx.created_at,
                )
            if existing_tx.reconciliation_status in ("ORDER_CREATION_IN_PROGRESS", "ORDER_CREATION_RECOVERING"):
                raise PaymentIdempotencyConflict(
                    f"Order creation recovery is actively in progress by another worker for proposal '{proposal_id}'."
                )
            raise PaymentVerificationError(
                f"Unable to claim order creation recovery for proposal '{proposal_id}'. Current status: {existing_tx.reconciliation_status}"
            )

        # 4. Enforce original durable idempotency key invariant
        order_key = existing_tx.idempotency_key
        if not order_key:
            raise PaymentVerificationError(f"Transaction '{existing_tx.id}' lacks a persisted provider idempotency key.")

        # 5. Enforce durable first-dispatch timestamp & provider retention contract
        first_dispatched_at = existing_tx.first_dispatched_at
        if not first_dispatched_at:
            existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            existing_tx.updated_at = now_utc
            db.add(
                AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovery blocked for Proposal #{proposal_id}: Missing first-dispatch timestamp prevents establishing provider idempotency window. Retaining reservation.",
                    entity_id=proposal_id,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state="MANUAL_REVIEW_REQUIRED",
                    details={"error": "MISSING_FIRST_DISPATCHED_AT", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            raise PaymentVerificationError(
                f"Cannot recover order for proposal '{proposal_id}': Missing first-dispatch timestamp prevents establishing provider idempotency window. Held for MANUAL_REVIEW_REQUIRED."
            )

        effective_retention = settings.get_effective_order_idempotency_retention_seconds()
        first_dispatched_utc = first_dispatched_at if first_dispatched_at.tzinfo else first_dispatched_at.replace(tzinfo=timezone.utc)
        age_seconds = (now_utc - first_dispatched_utc).total_seconds()

        if age_seconds < 0 or age_seconds >= effective_retention:
            existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            existing_tx.updated_at = now_utc
            db.add(
                AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovery blocked for Proposal #{proposal_id}: Provider idempotency retention window expired ({int(age_seconds)}s elapsed >= {effective_retention}s limit). Retaining reservation.",
                    entity_id=proposal_id,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state="MANUAL_REVIEW_REQUIRED",
                    details={
                        "error": "IDEMPOTENCY_WINDOW_EXPIRED",
                        "age_seconds": age_seconds,
                        "effective_retention_seconds": effective_retention,
                        "reconciliation_status": "MANUAL_REVIEW_REQUIRED",
                    },
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            raise PaymentVerificationError(
                f"Cannot recover order for proposal '{proposal_id}': Provider idempotency retention window of {effective_retention}s expired ({int(age_seconds)}s elapsed). Automatic recovery prohibited; held for MANUAL_REVIEW_REQUIRED."
            )

        # 6. Local simulated order check (simulation-only)
        # In simulation mode, check in-memory simulated order registry.
        # NOTE: PayPal REST API v2 has no search endpoint for idempotency keys.
        existing_provider_order = None
        if self.client.is_simulated:
            existing_provider_order = await self.client.lookup_simulated_order_by_idempotency_key(order_key)

        if existing_provider_order:
            order_id = existing_provider_order.get("id")
            if not order_id:
                existing_tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                existing_tx.updated_at = now_utc
                await db.commit()
                raise PaymentVerificationError("Provider returned simulated order missing order ID.")

            # Validate custom_id and amounts
            pu_list = existing_provider_order.get("purchase_units", [])
            pu = pu_list[0] if pu_list else {}
            custom_id = pu.get("custom_id")
            if custom_id and custom_id != proposal.id:
                existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                existing_tx.updated_at = now_utc
                await db.commit()
                raise PaymentVerificationError(
                    f"Provider custom_id '{custom_id}' does not match proposal '{proposal.id}'. Held for manual review."
                )

            # Recompute authoritative fingerprint
            _, authoritative_hash = recompute_proposal_fingerprint(proposal)
            if not proposal.fingerprint or proposal.fingerprint.sha256_hash != authoritative_hash:
                existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                existing_tx.updated_at = now_utc
                await db.commit()
                raise PaymentVerificationError(
                    "Fingerprint mismatch during order recovery. Held for manual review."
                )

            order_status = existing_provider_order.get("status", "CREATED")
            approval_url = None
            for link in existing_provider_order.get("links", []):
                if link.get("rel") == "approve":
                    approval_url = link.get("href")
                    break

            existing_tx.paypal_order_id = order_id
            existing_tx.paypal_status = order_status
            existing_tx.paypal_approval_url = approval_url
            existing_tx.reconciliation_status = "ORDER_CREATED"
            existing_tx.state = TransactionLifecycleState.PAYMENT_PENDING
            existing_tx.updated_at = now_utc
            proposal.state = TransactionLifecycleState.PAYMENT_PENDING
            proposal.updated_at = existing_tx.updated_at

            db.add(
                AuditEvent(
                    timestamp=existing_tx.updated_at,
                    event_type=AuditEventType.PAYPAL_ORDER_CREATED,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovered simulated PayPal Order #{order_id} for Proposal #{proposal.id} via local simulation registry.",
                    entity_id=proposal.id,
                    fingerprint=authoritative_hash,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
                    details={
                        "paypal_order_id": order_id,
                        "order_status": order_status,
                        "total_cents": proposal.total_cents,
                        "currency": proposal.currency,
                        "recovery_action": "LOCAL_SIMULATION_RECOVERED",
                    },
                    severity=SeverityLevel.INFO,
                )
            )
            await db.commit()
            await db.refresh(existing_tx)

            return PayPalOrderResponse(
                order_id=order_id,
                status=order_status,
                intent="CAPTURE",
                approval_url=approval_url,
                total_cents=proposal.total_cents,
                currency=proposal.currency,
                proposal_id=proposal.id,
                fingerprint_sha256=authoritative_hash,
                is_simulated=self.client.is_simulated,
                created_at=existing_tx.updated_at,
            )

        # 7. Same-Key POST Idempotency Replay Within Verified Retention Window
        # Provider-side request-id search does NOT exist in PayPal Orders v2.
        # Instead, within the 6-hour (or extended 72-hour) retention window, PayPal guarantees that
        # repeating POST /v2/checkout/orders with the identical PayPal-Request-Id returns the existing
        # order idempotently without creating a duplicate order.
        # Before replaying the POST, all local authorization and safety invariants are strictly revalidated.

        # Invariant 1: Denied/rejected proposals cannot proceed
        deny_eval_query = (
            select(PolicyEvaluationRecord)
            .where(
                PolicyEvaluationRecord.proposal_id == proposal_id,
                PolicyEvaluationRecord.decision == PolicyDecision.DENY,
            )
        )
        deny_eval_res = await db.execute(deny_eval_query)
        if deny_eval_res.scalars().first() or proposal.state == TransactionLifecycleState.REJECTED:
            existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            existing_tx.updated_at = now_utc
            db.add(
                AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovery blocked for Proposal #{proposal_id}: Proposal was DENIED or REJECTED. Ambient provider order status is ambiguous; retaining reservation for manual review.",
                    entity_id=proposal_id,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state="MANUAL_REVIEW_REQUIRED",
                    details={"error": "POLICY_DENIED_AMBIGUOUS_RECOVERY", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            raise PaymentPolicyDeniedError(
                "Cannot recover order: Proposal was DENIED or REJECTED by policy. Ambient provider order status is ambiguous; held for MANUAL_REVIEW_REQUIRED."
            )

        # Invariant 2: Recompute and re-verify fingerprint integrity
        _, authoritative_hash = recompute_proposal_fingerprint(proposal)
        if (
            not proposal.fingerprint
            or proposal.fingerprint.sha256_hash != authoritative_hash
            or existing_tx.fingerprint_sha256 != authoritative_hash
        ):
            existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            existing_tx.updated_at = now_utc
            db.add(
                AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovery blocked for Proposal #{proposal_id}: Fingerprint mismatch. Held for manual review with reservation retained.",
                    entity_id=proposal_id,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state="MANUAL_REVIEW_REQUIRED",
                    details={"error": "FINGERPRINT_MISMATCH_AMBIGUOUS_RECOVERY", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            raise PaymentVerificationError(
                "Fingerprint mismatch during recovery revalidation. Held for MANUAL_REVIEW_REQUIRED."
            )

        # Invariant 3: Re-verify Approval validity and expiration
        approval_query = (
            select(Approval)
            .where(Approval.fingerprint_hash == authoritative_hash)
            .order_by(desc(Approval.created_at))
        )
        app_res = await db.execute(approval_query)
        approval = app_res.scalars().first()
        if not approval or approval.is_revoked:
            existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            existing_tx.updated_at = now_utc
            db.add(
                AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovery blocked for Proposal #{proposal_id}: Underlying approval is missing or revoked. Provider order status is ambiguous; retaining reservation for manual review.",
                    entity_id=proposal_id,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state="MANUAL_REVIEW_REQUIRED",
                    details={"error": "APPROVAL_REVOKED_AMBIGUOUS_RECOVERY", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            raise PaymentVerificationError(
                "Cannot recover order: Underlying approval is missing or revoked. Provider order status is ambiguous; held for MANUAL_REVIEW_REQUIRED."
            )

        appr_expires_utc = approval.expires_at
        if appr_expires_utc and appr_expires_utc.tzinfo is None:
            appr_expires_utc = appr_expires_utc.replace(tzinfo=timezone.utc)
        if appr_expires_utc and appr_expires_utc < now_utc:
            existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            existing_tx.updated_at = now_utc
            db.add(
                AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Recovery blocked for Proposal #{proposal_id}: Underlying approval has expired. Provider order status is ambiguous; retaining reservation for manual review.",
                    entity_id=proposal_id,
                    previous_state="ORDER_CREATION_RECOVERING",
                    new_state="MANUAL_REVIEW_REQUIRED",
                    details={"error": "APPROVAL_EXPIRED_AMBIGUOUS_RECOVERY", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            raise PaymentVerificationError(
                "Cannot recover order: Underlying approval has expired. Provider order status is ambiguous; held for MANUAL_REVIEW_REQUIRED."
            )

        # Invariant 4: Re-verify Consent Policy validity
        if approval.consent_policy_id:
            pol_res = await db.execute(
                select(ConsentPolicy).where(ConsentPolicy.id == approval.consent_policy_id)
            )
            pol = pol_res.scalar_one_or_none()
            if pol:
                pol_valid_utc = pol.valid_until
                if pol_valid_utc and pol_valid_utc.tzinfo is None:
                    pol_valid_utc = pol_valid_utc.replace(tzinfo=timezone.utc)
                if pol.is_revoked or (pol_valid_utc and pol_valid_utc < now_utc):
                    existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                    existing_tx.updated_at = now_utc
                    db.add(
                        AuditEvent(
                            timestamp=now_utc,
                            event_type=AuditEventType.STATE_TRANSITION,
                            actor=proposal.user_id,
                            role="PAYPAL_GATEWAY",
                            module=to_str(proposal.module),
                            summary=f"Recovery blocked for Proposal #{proposal_id}: Consent policy was revoked or expired. Provider order status is ambiguous; retaining reservation for manual review.",
                            entity_id=proposal_id,
                            previous_state="ORDER_CREATION_RECOVERING",
                            new_state="MANUAL_REVIEW_REQUIRED",
                            details={"error": "CONSENT_INVALID_AMBIGUOUS_RECOVERY", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                            severity=SeverityLevel.ALERT,
                        )
                    )
                    await db.commit()
                    raise PaymentVerificationError(
                        "Cannot recover order: Underlying consent policy was revoked or expired. Provider order status is ambiguous; held for MANUAL_REVIEW_REQUIRED."
                    )

        # Invariant 5: Re-verify held CARE budget reservation
        if to_str(proposal.module) == "CARE" or proposal.care_profile_id:
            resv = await db.scalar(
                select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id)
            )
            if not resv or resv.status != ReservationStatus.HELD or resv.amount_cents != proposal.total_cents:
                existing_tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                existing_tx.updated_at = now_utc
                db.add(
                    AuditEvent(
                        timestamp=now_utc,
                        event_type=AuditEventType.STATE_TRANSITION,
                        actor=proposal.user_id,
                        role="PAYPAL_GATEWAY",
                        module=to_str(proposal.module),
                        summary=f"Recovery blocked for Proposal #{proposal_id}: Budget reservation missing or not in HELD status. Held for manual review.",
                        entity_id=proposal_id,
                        previous_state="ORDER_CREATION_RECOVERING",
                        new_state="MANUAL_REVIEW_REQUIRED",
                        details={"error": "BUDGET_RESERVATION_INVALID", "reconciliation_status": "MANUAL_REVIEW_REQUIRED"},
                        severity=SeverityLevel.ALERT,
                    )
                )
                await db.commit()
                raise PaymentVerificationError("Cannot recover order: Budget reservation missing or not in HELD status. Held for MANUAL_REVIEW_REQUIRED.")

        # All invariants revalidated -> Pre-network commit to release session before dispatch
        items_dict = [
            {
                "name": it.name,
                "quantity": it.quantity,
                "unit_price_cents": it.unit_price_cents,
                "total_cents": it.total_cents,
            }
            for it in proposal.items
        ]
        existing_tx.reconciliation_status = "ORDER_CREATION_IN_PROGRESS"
        existing_tx.updated_at = now_utc
        # CRITICAL: Never reset first_dispatched_at during recovery!
        await db.commit()

        # Outbound same-key idempotency replay dispatch with the ORIGINAL STABLE IDEMPOTENCY KEY
        order_resp = None
        order_err = None
        try:
            order_resp = await self.client.create_order(
                total_cents=proposal.total_cents,
                currency=proposal.currency,
                items=items_dict,
                custom_id=proposal.id,
                idempotency_key=order_key,
                subtotal_cents=proposal.subtotal_cents,
                tax_cents=proposal.tax_cents,
                shipping_cents=proposal.shipping_cents,
            )
        except Exception as exc:
            order_err = exc

        # Post-dispatch handling
        now_post_utc = datetime.now(timezone.utc)
        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        tx = tx_res.scalars().first()

        prop_query = select(PurchaseProposal).where(PurchaseProposal.id == proposal_id)
        prop_res = await db.execute(prop_query)
        prop = prop_res.scalars().first()

        if order_err:
            err_msg = str(order_err)
            is_definitive, reason = classify_order_creation_error(order_err)
            if not is_definitive:
                if tx:
                    tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                    tx.updated_at = now_post_utc
                db.add(
                    AuditEvent(
                        timestamp=now_post_utc,
                        event_type=AuditEventType.STATE_TRANSITION,
                        actor=proposal.user_id,
                        role="PAYPAL_GATEWAY",
                        module=to_str(proposal.module),
                        summary=f"Recovery idempotency replay ambiguous for Proposal #{proposal_id}. Retaining reservation: {reason}",
                        entity_id=proposal_id,
                        previous_state="ORDER_CREATION_IN_PROGRESS",
                        new_state="PAYMENT_PENDING",
                        details={"error": err_msg, "classification": reason, "reconciliation_status": "RECONCILIATION_REQUIRED"},
                        severity=SeverityLevel.WARNING,
                    )
                )
                await db.commit()
                raise order_err
            else:
                # Definite 4xx failure confirming provider non-creation
                if tx:
                    tx.state = TransactionLifecycleState.FAILED
                    tx.paypal_status = "FAILED"
                    tx.reconciliation_status = "CREATION_FAILED"
                    tx.updated_at = now_post_utc
                if prop:
                    prop.state = TransactionLifecycleState.FAILED
                    prop.updated_at = now_post_utc

                await release_care_budget(db, proposal_id, is_simulated=False)
                db.add(
                    AuditEvent(
                        timestamp=now_post_utc,
                        event_type=AuditEventType.STATE_TRANSITION,
                        actor=proposal.user_id,
                        role="PAYPAL_GATEWAY",
                        module=to_str(proposal.module),
                        summary=f"Recovery idempotency replay definitely failed for Proposal #{proposal_id}. Released reservation: {err_msg}",
                        entity_id=proposal_id,
                        previous_state="ORDER_CREATION_IN_PROGRESS",
                        new_state=to_str(TransactionLifecycleState.FAILED),
                        details={"error": err_msg},
                        severity=SeverityLevel.ALERT,
                    )
                )
                await db.commit()
                raise order_err

        # Order creation / idempotency replay succeeded
        order_id = order_resp.get("id")
        if not order_id:
            if tx:
                tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                tx.updated_at = now_post_utc
            await db.commit()
            raise PaymentVerificationError("PayPal returned order response missing order ID.")

        order_status = order_resp.get("status", "CREATED")
        approval_url = None
        for link in order_resp.get("links", []):
            if link.get("rel") == "approve":
                approval_url = link.get("href")
                break

        if tx:
            tx.paypal_order_id = order_id
            tx.paypal_status = order_status
            tx.paypal_approval_url = approval_url
            tx.reconciliation_status = "ORDER_CREATED"
            tx.state = TransactionLifecycleState.PAYMENT_PENDING
            tx.updated_at = now_post_utc

        if prop:
            prop.state = TransactionLifecycleState.PAYMENT_PENDING
            prop.updated_at = now_post_utc

        db.add(
            AuditEvent(
                timestamp=now_post_utc,
                event_type=AuditEventType.PAYPAL_ORDER_CREATED,
                actor=proposal.user_id,
                role="PAYPAL_GATEWAY",
                module=to_str(proposal.module),
                summary=f"Recovered PayPal Sandbox Order #{order_id} for Proposal #{proposal.id} via same-key idempotency replay ({int(age_seconds)}s elapsed / {effective_retention}s limit).",
                entity_id=proposal.id,
                fingerprint=authoritative_hash,
                previous_state="ORDER_CREATION_IN_PROGRESS",
                new_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
                details={
                    "paypal_order_id": order_id,
                    "order_status": order_status,
                    "total_cents": proposal.total_cents,
                    "currency": proposal.currency,
                    "recovery_action": "SAME_KEY_IDEMPOTENCY_REPLAY",
                    "age_seconds": age_seconds,
                    "retention_window_seconds": effective_retention,
                },
                severity=SeverityLevel.INFO,
            )
        )
        await db.commit()
        if tx:
            await db.refresh(tx)

        return PayPalOrderResponse(
            order_id=order_id,
            status=order_status,
            intent="CAPTURE",
            approval_url=approval_url,
            total_cents=proposal.total_cents,
            currency=proposal.currency,
            proposal_id=proposal.id,
            fingerprint_sha256=authoritative_hash,
            is_simulated=self.client.is_simulated,
            created_at=now_post_utc,
        )

    async def reconcile_timed_out_capture(
        self,
        db: AsyncSession,
        proposal_id: str,
        paypal_order_id: Optional[str] = None,
    ) -> PayPalCaptureResponse:
        """
        Authoritative timeout and disconnected-capture reconciliation.
        Audits and verifies provider order status against local transaction invariants.

        Enforces all 13 security verification dimensions:
        1. Preserves original persisted idempotency key.
        2. Retrieves provider order via get_order().
        3. Verifies returned order ID matches persisted transaction order ID.
        4. Verifies order custom_id matches MERIDIAN proposal ID.
        5. Verifies provider capture status is COMPLETED.
        6. Extracts valid provider capture ID.
        7. Verifies capture amount matches authoritative persisted total_cents.
        8. Verifies currency matches authoritative persisted currency.
        9. Re-verifies cryptographic transaction fingerprint against database items.
        10. Re-verifies approval validity, expiry, and consent unrevoked.
        11. Verifies actual persisted proposal and transaction lifecycle states.
        12. If capture is confirmed externally but local state is invalid (e.g. cancelled),
            preserves evidence without overwriting state, marking MANUAL_REVIEW_REQUIRED.
        13. Only transitions to genuine PAID when all checks pass.
        """
        now_utc = datetime.now(timezone.utc)

        # 1. Fetch proposal and transaction
        query = (
            select(PurchaseProposal)
            .where(PurchaseProposal.id == proposal_id)
            .options(
                selectinload(PurchaseProposal.items),
                selectinload(PurchaseProposal.fingerprint),
                selectinload(PurchaseProposal.transactions),
            )
        )
        result = await db.execute(query)
        proposal = result.scalar_one_or_none()
        if not proposal:
            raise PaymentVerificationError(f"Purchase proposal '{proposal_id}' not found.")

        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        tx = tx_res.scalars().first()
        if not tx:
            raise PaymentVerificationError(f"No transaction record found for proposal '{proposal_id}'.")

        target_order_id = paypal_order_id or tx.paypal_order_id
        if not target_order_id:
            raise PaymentVerificationError(f"Proposal '{proposal_id}' has no associated PayPal order ID.")

        if tx.paypal_order_id and tx.paypal_order_id != target_order_id:
            raise PaymentVerificationError(
                f"Order ID mismatch: Persisted transaction order ID '{tx.paypal_order_id}' "
                f"does not match reconciliation target '{target_order_id}'."
            )

        proposal_state = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )
        deny_eval_query = (
            select(PolicyEvaluationRecord)
            .where(
                PolicyEvaluationRecord.proposal_id == proposal_id,
                PolicyEvaluationRecord.decision == PolicyDecision.DENY,
            )
        )
        deny_eval_res = await db.execute(deny_eval_query)
        if deny_eval_res.scalars().first() or proposal_state == TransactionLifecycleState.REJECTED:
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentPolicyDeniedError(
                "Reconciliation security block: Proposal has a Trust Engine 'DENY' policy decision. Cannot transition to PAID."
            )

        # 2. Retrieve provider order via get_order probe
        try:
            order_probe = await self.client.get_order(target_order_id)
        except Exception as probe_err:
            logger.error(f"Reconciliation probe failed for order {target_order_id}: {probe_err}")
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"PayPal capture timed out and probe failed: {probe_err}. "
                "Transaction marked RECONCILIATION_REQUIRED. Funds unconfirmed."
            )

        if not order_probe or not isinstance(order_probe, dict):
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError("Reconciliation failed: Empty or malformed provider response.")

        # 3. Verify returned order ID
        probe_order_id = order_probe.get("id")
        if not probe_order_id or probe_order_id != target_order_id:
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation mismatch: Provider order ID '{probe_order_id}' does not match expected '{target_order_id}'."
            )

        # 4. Check purchase units and captures
        purchase_units = order_probe.get("purchase_units")
        if not purchase_units or not isinstance(purchase_units, list) or len(purchase_units) == 0:
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation failed: Missing purchase units in order probe response for '{target_order_id}'."
            )

        unit = purchase_units[0]

        # Verify custom_id matches proposal ID if present
        custom_id = unit.get("custom_id")
        if custom_id and custom_id != proposal.id:
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation mismatch: Provider custom_id '{custom_id}' does not match proposal '{proposal.id}'."
            )

        # Verify unit-level amount and currency if present
        unit_amount = unit.get("amount")
        if unit_amount and isinstance(unit_amount, dict):
            u_curr = unit_amount.get("currency_code")
            u_val = unit_amount.get("value")
            if u_curr and u_curr.upper() != tx.currency.upper():
                tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                tx.paypal_status = "RECONCILIATION_REQUIRED"
                tx.updated_at = now_utc
                await db.commit()
                raise PaymentCaptureError(
                    f"Reconciliation mismatch: Provider currency '{u_curr}' does not match expected '{tx.currency}'."
                )
            if u_val is not None:
                try:
                    u_cents = parse_monetary_to_minor_units(u_val, currency=tx.currency, allow_zero=False)
                except (MonetaryValidationError, ValueError, TypeError) as mve:
                    tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                    tx.paypal_status = "RECONCILIATION_REQUIRED"
                    tx.updated_at = now_utc
                    await db.commit()
                    raise PaymentCaptureError(f"Reconciliation failed: Malformed order amount value '{u_val}': {mve}")
                if u_cents != tx.total_cents:
                    tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                    tx.paypal_status = "RECONCILIATION_REQUIRED"
                    tx.updated_at = now_utc
                    await db.commit()
                    raise PaymentCaptureError(
                        f"Reconciliation mismatch: Provider amount {u_cents} cents does not match expected {tx.total_cents} cents."
                    )

        payments = unit.get("payments", {}) if isinstance(unit, dict) else {}
        captures = payments.get("captures", []) if isinstance(payments, dict) else []
        if not captures or not isinstance(captures, list) or len(captures) == 0:
            order_status = order_probe.get("status")
            if order_status in ("APPROVED", "CREATED"):
                # Outcome 2: Positively confirmed uncaptured and eligible for controlled capture retry
                logger.info(
                    f"Order '{target_order_id}' confirmed uncaptured with status '{order_status}'."
                )
                raise ConfirmedUncapturedOrderError(
                    f"Reconciliation: No capture record found in provider order '{target_order_id}'. Order confirmed uncaptured with status '{order_status}'.",
                    order_id=target_order_id,
                    order_data=order_probe,
                )
            else:
                # Outcome 3: Ineligible or unexpected status without captures (e.g. VOIDED, SAVED, COMPLETED without capture details)
                tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                tx.paypal_status = "RECONCILIATION_REQUIRED"
                tx.updated_at = now_utc
                await db.commit()
                raise PaymentCaptureError(
                    f"Reconciliation: Order status '{order_status}' has no captures and is ineligible for capture retry."
                )

        cap = captures[0]
        capture_id = cap.get("id")
        capture_status = cap.get("status")

        if not capture_id or not isinstance(capture_id, str) or not capture_id.strip():
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError("Reconciliation failed: Malformed capture data: missing capture ID.")

        if capture_status != "COMPLETED":
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation: Provider capture status is '{capture_status}', expected 'COMPLETED'."
            )

        # 5. Verify amount and currency
        amount_obj = cap.get("amount") or unit.get("amount")
        if not amount_obj or not isinstance(amount_obj, dict):
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError("Reconciliation failed: Missing amount in provider capture data.")

        probe_curr = amount_obj.get("currency_code")
        probe_val = amount_obj.get("value")
        if not probe_curr or probe_val is None:
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError("Reconciliation failed: Missing currency or amount value in capture data.")

        if probe_curr.upper() != tx.currency.upper():
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation mismatch: Provider currency '{probe_curr}' does not match expected '{tx.currency}'."
            )

        try:
            probe_cents = parse_monetary_to_minor_units(probe_val, currency=tx.currency, allow_zero=False)
        except (MonetaryValidationError, ValueError, TypeError) as mve:
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(f"Reconciliation failed: Malformed capture amount value '{probe_val}': {mve}")

        if probe_cents != tx.total_cents:
            tx.reconciliation_status = "RECONCILIATION_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation mismatch: Provider amount {probe_cents} cents does not match expected {tx.total_cents} cents."
            )

        # 6. Recompute and verify cryptographic fingerprint
        _, authoritative_hash = recompute_proposal_fingerprint(proposal)
        if (
            not proposal.fingerprint
            or proposal.fingerprint.sha256_hash != authoritative_hash
            or tx.fingerprint_sha256 != authoritative_hash
        ):
            # Preserves evidence of external capture without validating corrupted transaction
            tx.paypal_capture_id = capture_id
            tx.paypal_status = "COMPLETED"
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentVerificationError(
                "SECURITY ALERT: Fingerprint mismatch during reconciliation. Provider capture recorded but transaction held in MANUAL_REVIEW_REQUIRED."
            )

        # 7. Re-verify approval integrity & consent validity
        app_query = (
            select(Approval)
            .where(Approval.fingerprint_hash == authoritative_hash)
            .order_by(desc(Approval.created_at))
        )
        app_res = await db.execute(app_query)
        approval = app_res.scalars().first()
        approval_dict = None
        if approval:
            approval_dict = {
                "fingerprint_hash": approval.fingerprint_hash,
                "expires_at": approval.expires_at,
                "is_revoked": approval.is_revoked,
                "revoked_reason": approval.revoked_reason,
            }

        policy_dict = None
        if approval and approval.consent_policy_id:
            pol_res = await db.execute(
                select(ConsentPolicy).where(ConsentPolicy.id == approval.consent_policy_id)
            )
            pol = pol_res.scalar_one_or_none()
            if pol:
                policy_dict = {
                    "is_revoked": pol.is_revoked,
                    "expires_at": getattr(pol, "valid_until", getattr(pol, "expires_at", None)),
                }

        verif_result = verify_approval_integrity(
            approval=approval_dict,
            expected_fingerprint_hash=authoritative_hash,
            policy=policy_dict,
        )
        if not verif_result.is_valid:
            tx.paypal_capture_id = capture_id
            tx.paypal_status = "COMPLETED"
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentVerificationError(
                f"Reconciliation security block: {verif_result.reason}. Provider capture preserved but held for MANUAL_REVIEW_REQUIRED."
            )

        # 8. Check actual persisted lifecycle states
        proposal_state = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )
        tx_state = (
            tx.state
            if isinstance(tx.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(tx.state))
        )
        if proposal_state != TransactionLifecycleState.PAYMENT_PENDING or tx_state != TransactionLifecycleState.PAYMENT_PENDING:
            # External capture confirmed, but local transaction became cancelled or invalid!
            # Do NOT silently overwrite lifecycle state with PAID!
            tx.paypal_capture_id = capture_id
            tx.paypal_status = "COMPLETED"
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentCaptureError(
                f"Reconciliation state conflict: External capture confirmed ({capture_id}), but local proposal is in '{proposal_state.value}' and tx is in '{tx_state.value}'. "
                "External capture recorded; payment held in MANUAL_REVIEW_REQUIRED."
            )

        # 9. All 13 dimensions verified -> transition to genuine PAID (or SIMULATED if mock)
        is_mock_sim = bool(self.client.is_simulated or order_probe.get("_is_simulated") or order_probe.get("is_simulated"))
        if is_mock_sim:
            # Simulated payments remain SIMULATED and SUBMITTED, NEVER marked genuine PAID
            tx.paypal_capture_id = capture_id
            tx.paypal_status = "SIMULATED"
            tx.reconciliation_status = "RECONCILED_SUCCESS"
            tx.is_simulated = True
            tx.updated_at = now_utc
            # Finding 2 & 3: Safely release genuine budget reservation, never increment genuine monthly_spent_cents
            await release_care_budget(db, proposal.id, is_simulated=True)
        else:
            try:
                validate_transition(proposal_state, TransactionLifecycleState.PAID)
                validate_transition(tx_state, TransactionLifecycleState.PAID)
                proposal.state = TransactionLifecycleState.PAID
                tx.state = TransactionLifecycleState.PAID
            except IllegalStateTransitionError as exc:
                tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                await db.commit()
                raise PaymentCaptureError(str(exc))

            tx.paypal_capture_id = capture_id
            tx.paypal_status = "COMPLETED"
            tx.reconciliation_status = "RECONCILED_SUCCESS"
            tx.is_simulated = False
            tx.updated_at = now_utc
            # Finding 2 & 3: Real COMPLETED capture consumes held budget reservation and increments monthly_spent_cents once
            await consume_care_budget(db, proposal.id)

        final_status = "SIMULATED" if is_mock_sim else "COMPLETED"
        final_state_str = to_str(proposal_state) if is_mock_sim else to_str(TransactionLifecycleState.PAID)

        audit = AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.PAYPAL_ORDER_CAPTURED,
            actor=proposal.user_id,
            role="PAYPAL_GATEWAY",
            module=to_str(proposal.module),
            summary=f"Reconciled verified PayPal payment #{capture_id} for Order #{target_order_id} (Proposal #{proposal.id}).",
            entity_id=proposal.id,
            fingerprint=authoritative_hash,
            previous_state=to_str(proposal_state),
            new_state=final_state_str,
            details={
                "paypal_order_id": target_order_id,
                "paypal_capture_id": capture_id,
                "capture_status": final_status,
                "total_cents": proposal.total_cents,
                "currency": proposal.currency,
                "reconciliation_status": "RECONCILED_SUCCESS",
                "is_simulated": is_mock_sim,
            },
            severity=SeverityLevel.SUCCESS,
        )
        db.add(audit)
        await db.commit()
        await db.refresh(tx)

        return PayPalCaptureResponse(
            capture_id=capture_id,
            order_id=target_order_id,
            status=final_status,
            total_cents=proposal.total_cents,
            currency=proposal.currency,
            proposal_id=proposal.id,
            fingerprint_sha256=authoritative_hash,
            is_simulated=is_mock_sim,
            reconciliation_status="RECONCILED_SUCCESS",
            captured_at=now_utc,
        )

    async def _revalidate_pre_dispatch(
        self,
        db: AsyncSession,
        proposal: PurchaseProposal,
        tx: Transaction,
    ) -> None:
        """
        Revalidates cryptographic fingerprint, approval validity, consent validity,
        and transaction lifecycle eligibility immediately prior to dispatching capture retry.
        Fails closed by holding the transaction in MANUAL_REVIEW_REQUIRED.
        """
        now_utc = datetime.now(timezone.utc)

        # 1. Recompute and verify cryptographic fingerprint
        _, authoritative_hash = recompute_proposal_fingerprint(proposal)
        if (
            not proposal.fingerprint
            or proposal.fingerprint.sha256_hash != authoritative_hash
            or tx.fingerprint_sha256 != authoritative_hash
        ):
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentVerificationError(
                "SECURITY ALERT: Fingerprint mismatch before capture retry dispatch. Transaction held in MANUAL_REVIEW_REQUIRED."
            )

        # 2. Re-verify approval integrity & consent validity
        app_query = (
            select(Approval)
            .where(Approval.fingerprint_hash == authoritative_hash)
            .order_by(desc(Approval.created_at))
        )
        app_res = await db.execute(app_query)
        approval = app_res.scalars().first()
        if not approval:
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentVerificationError("No approval record found before capture retry dispatch.")

        approval_dict = {
            "fingerprint_hash": approval.fingerprint_hash,
            "expires_at": approval.expires_at,
            "is_revoked": approval.is_revoked,
            "revoked_reason": approval.revoked_reason,
        }

        policy_dict = None
        if approval.consent_policy_id:
            pol_res = await db.execute(
                select(ConsentPolicy).where(ConsentPolicy.id == approval.consent_policy_id)
            )
            pol = pol_res.scalar_one_or_none()
            if pol:
                if pol.is_revoked:
                    tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                    tx.paypal_status = "RECONCILIATION_REQUIRED"
                    tx.updated_at = now_utc
                    await db.commit()
                    raise PaymentVerificationError("Consent policy was revoked before capture retry dispatch.")
                policy_dict = {
                    "is_revoked": pol.is_revoked,
                    "expires_at": getattr(pol, "valid_until", getattr(pol, "expires_at", None)),
                }

        verif_result = verify_approval_integrity(
            approval=approval_dict,
            expected_fingerprint_hash=authoritative_hash,
            policy=policy_dict,
        )
        if not verif_result.is_valid:
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentVerificationError(
                f"Reconciliation security block before capture retry dispatch: {verif_result.reason}"
            )

        # 3. Check actual persisted lifecycle states
        proposal_state = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )
        tx_state = (
            tx.state
            if isinstance(tx.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(tx.state))
        )
        if (
            proposal_state != TransactionLifecycleState.PAYMENT_PENDING
            or tx_state != TransactionLifecycleState.PAYMENT_PENDING
        ):
            tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
            tx.paypal_status = "RECONCILIATION_REQUIRED"
            tx.updated_at = now_utc
            await db.commit()
            raise PaymentVerificationError(
                f"Lifecycle conflict before capture retry dispatch: proposal is '{proposal_state.value}', tx is '{tx_state.value}'."
            )

    async def capture_order_for_proposal(
        self,
        db: AsyncSession,
        proposal_id: str,
        paypal_order_id: str,
        idempotency_key: Optional[str] = None,
    ) -> PayPalCaptureResponse:
        """
        Re-verifies all invariants server-side and executes PayPal Sandbox capture.
        1. Acquires per-proposal concurrency lock to prevent double-capture race conditions.
        2. Checks proposal and transaction match paypal_order_id.
        3. Recalculates SHA-256 fingerprint from DB items; detects tampering.
        4. If tampered, revokes approval, rejects transaction, emits TAMPER_DETECTED audit, and aborts.
        5. Re-verifies approval validity.
        6. Reconciles timeouts via PayPal get_order probe.
        7. For simulated mode: records SIMULATED without transitioning to PAID.
        8. For verified PayPal sandbox: executes capture, validates dynamic transition to PAID.
        """
        if proposal_id not in self._local_locks:
            self._local_locks[proposal_id] = asyncio.Lock()

        async with self._local_locks[proposal_id]:
            now_utc = datetime.now(timezone.utc)

            # 1. Fetch proposal and transaction
            query = (
                select(PurchaseProposal)
                .where(PurchaseProposal.id == proposal_id)
                .options(
                    selectinload(PurchaseProposal.items),
                    selectinload(PurchaseProposal.fingerprint),
                    selectinload(PurchaseProposal.transactions),
                )
            )
            result = await db.execute(query)
            proposal = result.scalar_one_or_none()

            if not proposal:
                raise PaymentVerificationError(f"Purchase proposal '{proposal_id}' not found.")

            tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
            tx_res = await db.execute(tx_query)
            tx = tx_res.scalars().first()

            if not tx:
                raise PaymentVerificationError(f"No transaction record found for proposal '{proposal_id}'.")

            if tx.paypal_order_id != paypal_order_id:
                raise PaymentVerificationError(
                    f"Order ID mismatch: Persisted transaction order ID '{tx.paypal_order_id}' "
                    f"does not match capture request '{paypal_order_id}'."
                )

            # Idempotent return if already paid or simulated
            if tx.paypal_capture_id and (
                tx.state == TransactionLifecycleState.PAID
                or tx.paypal_status in ("COMPLETED", "SIMULATED")
            ):
                return PayPalCaptureResponse(
                    capture_id=tx.paypal_capture_id,
                    order_id=tx.paypal_order_id,
                    status=tx.paypal_status or "COMPLETED",
                    total_cents=tx.total_cents,
                    currency=tx.currency,
                    proposal_id=proposal.id,
                    fingerprint_sha256=tx.fingerprint_sha256,
                    is_simulated=tx.is_simulated,
                    reconciliation_status=tx.reconciliation_status,
                    captured_at=tx.updated_at,
                )

            # Dynamic lifecycle validation: must currently be in PAYMENT_PENDING
            proposal_state = (
                proposal.state
                if isinstance(proposal.state, TransactionLifecycleState)
                else TransactionLifecycleState(str(proposal.state))
            )
            tx_state = (
                tx.state
                if isinstance(tx.state, TransactionLifecycleState)
                else TransactionLifecycleState(str(tx.state))
            )

            if proposal_state == TransactionLifecycleState.REJECTED:
                raise PaymentPolicyDeniedError("Cannot capture payment: Proposal has been REJECTED by policy.")

            deny_eval_query = (
                select(PolicyEvaluationRecord)
                .where(
                    PolicyEvaluationRecord.proposal_id == proposal_id,
                    PolicyEvaluationRecord.decision == PolicyDecision.DENY,
                )
            )
            deny_eval_res = await db.execute(deny_eval_query)
            if deny_eval_res.scalars().first():
                raise PaymentPolicyDeniedError("Cannot capture payment: Proposal has a Trust Engine 'DENY' policy decision.")

            if proposal_state != TransactionLifecycleState.PAYMENT_PENDING or tx_state != TransactionLifecycleState.PAYMENT_PENDING:
                raise PaymentVerificationError(
                    f"Cannot capture payment: Proposal is in '{proposal_state.value}' state and "
                    f"Transaction is in '{tx_state.value}' state. Expected PAYMENT_PENDING."
                )

            # 2. Recompute authoritative fingerprint and check for tampering
            _, authoritative_hash = recompute_proposal_fingerprint(proposal)

            if (
                not proposal.fingerprint
                or proposal.fingerprint.sha256_hash != authoritative_hash
                or tx.fingerprint_sha256 != authoritative_hash
            ):
                # TAMPER DETECTED: Revoke approval and mark FAILED
                app_query = select(Approval).where(
                    Approval.fingerprint_hash == (proposal.fingerprint.sha256_hash if proposal.fingerprint else "")
                )
                app_res = await db.execute(app_query)
                app_to_revoke = app_res.scalars().first()
                if app_to_revoke:
                    app_to_revoke.is_revoked = True
                    app_to_revoke.revoked_reason = "Tamper detected: persisted items altered after approval."

                proposal.state = TransactionLifecycleState.FAILED
                tx.state = TransactionLifecycleState.FAILED
                tx.updated_at = now_utc

                tamper_audit = AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.TAMPER_DETECTED,
                    actor="TRUST_ENGINE",
                    role="SECURITY_KERNEL",
                    module=to_str(proposal.module),
                    summary=f"SECURITY ALERT: Tampering detected prior to capture on proposal #{proposal.id}. Capture halted.",
                    entity_id=proposal.id,
                    fingerprint=authoritative_hash,
                    previous_state=to_str(proposal_state),
                    new_state=to_str(TransactionLifecycleState.FAILED),
                    details={
                        "stored_proposal_hash": proposal.fingerprint.sha256_hash if proposal.fingerprint else None,
                        "authoritative_hash": authoritative_hash,
                        "transaction_hash": tx.fingerprint_sha256,
                    },
                    severity=SeverityLevel.ALERT,
                )
                db.add(tamper_audit)
                await db.commit()

                raise PaymentVerificationError(
                    "SECURITY ALERT: Cryptographic fingerprint mismatch detected prior to PayPal capture. "
                    "Persisted transaction parameters have been altered. Payment blocked and approval revoked."
                )

            # 3. Check approval validity
            app_query = (
                select(Approval)
                .where(Approval.fingerprint_hash == authoritative_hash)
                .order_by(desc(Approval.created_at))
            )
            app_res = await db.execute(app_query)
            approval = app_res.scalars().first()

            approval_dict = None
            if approval:
                approval_dict = {
                    "fingerprint_hash": approval.fingerprint_hash,
                    "expires_at": approval.expires_at,
                    "is_revoked": approval.is_revoked,
                    "revoked_reason": approval.revoked_reason,
                }

            policy_dict = None
            if approval and approval.consent_policy_id:
                pol_res = await db.execute(
                    select(ConsentPolicy).where(ConsentPolicy.id == approval.consent_policy_id)
                )
                pol = pol_res.scalar_one_or_none()
                if pol:
                    policy_dict = {
                        "is_revoked": pol.is_revoked,
                        "expires_at": getattr(pol, "valid_until", getattr(pol, "expires_at", None)),
                    }

            verif_result = verify_approval_integrity(
                approval=approval_dict,
                expected_fingerprint_hash=authoritative_hash,
                policy=policy_dict,
            )

            if not verif_result.is_valid:
                tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                tx.updated_at = now_utc
                await db.commit()
                raise PaymentVerificationError(f"Approval integrity failure prior to capture: {verif_result.reason}")

            # 4. Authoritative server-side capture idempotency key
            # Reuse persisted key across retries, timeouts, and restarts; never let client-supplied key override.
            authoritative_cap_key = tx.capture_idempotency_key
            if not authoritative_cap_key:
                authoritative_cap_key = f"meridian-cap-{proposal.id[:8]}-{uuid.uuid4().hex[:12]}"

            prior_status = tx.paypal_status
            prior_recon_status = tx.reconciliation_status

            # 5. Atomic database-backed capture claim (cross-worker and cross-instance safety)
            stale_timeout_seconds = 60
            stale_cutoff = now_utc - timedelta(seconds=stale_timeout_seconds)

            claim_stmt = (
                update(Transaction)
                .where(
                    Transaction.id == tx.id,
                    Transaction.state == to_str(TransactionLifecycleState.PAYMENT_PENDING),
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
                    capture_idempotency_key=authoritative_cap_key,
                    version=Transaction.version + 1,
                    updated_at=now_utc,
                )
                .execution_options(synchronize_session=False)
            )
            claim_res = await db.execute(claim_stmt)
            await db.commit()

            if claim_res.rowcount == 0:
                # Failed to obtain claim: competing worker/process claimed or completed it
                await db.refresh(tx)
                if tx.paypal_capture_id and (
                    tx.state == TransactionLifecycleState.PAID
                    or tx.paypal_status in ("COMPLETED", "SIMULATED")
                ):
                    return PayPalCaptureResponse(
                        capture_id=tx.paypal_capture_id,
                        order_id=tx.paypal_order_id,
                        status=tx.paypal_status or "COMPLETED",
                        total_cents=tx.total_cents,
                        currency=tx.currency,
                        proposal_id=proposal.id,
                        fingerprint_sha256=tx.fingerprint_sha256,
                        is_simulated=tx.is_simulated,
                        reconciliation_status=tx.reconciliation_status,
                        captured_at=tx.updated_at,
                    )
                if tx.paypal_status == "CAPTURING":
                    raise PaymentIdempotencyConflict(
                        f"A capture operation is currently in progress for proposal '{proposal_id}' (Order '{paypal_order_id}'). "
                        "Concurrent capture claim rejected."
                    )
                raise PaymentVerificationError(
                    f"Unable to claim capture lock for proposal '{proposal_id}': transaction state is invalid or was concurrently altered."
                )

            # Check if order requires reconciliation before capture
            must_reconcile_first = (
                prior_status in ("CAPTURING", "RECONCILIATION_REQUIRED")
                or prior_recon_status == "RECONCILIATION_REQUIRED"
            )
            if must_reconcile_first:
                logger.info(
                    f"Order {paypal_order_id} requires reconciliation probe first "
                    f"(prior_status='{prior_status}', reconciliation_status='{prior_recon_status}')."
                )
                try:
                    return await self.reconcile_timed_out_capture(
                        db=db,
                        proposal_id=proposal_id,
                        paypal_order_id=paypal_order_id,
                    )
                except ConfirmedUncapturedOrderError as cuoe:
                    logger.info(
                        f"Order {paypal_order_id} positively confirmed uncaptured by provider ({cuoe}). "
                        f"Proceeding with controlled capture retry using persisted key '{authoritative_cap_key}'."
                    )
                    # Outcome 2: Positively confirmed uncaptured.
                    # Revalidate approval, consent, fingerprint, lifecycle eligibility before dispatch
                    await self._revalidate_pre_dispatch(db, proposal, tx)
                # Outcome 3 (Ambiguous / Unavailable): Any other exception (PaymentCaptureError, PaymentVerificationError)
                # is NOT caught here and bubbles up, keeping transaction in RECONCILIATION_REQUIRED or MANUAL_REVIEW_REQUIRED.

            # 6. Execute PayPal Capture with Timeout Reconciliation
            try:
                capture_resp = await self.client.capture_order(paypal_order_id, idempotency_key=authoritative_cap_key)
            except PaymentCaptureError as pce:
                err_msg = str(pce).lower()
                if "timeout" in err_msg or "network" in err_msg:
                    logger.warning(f"Capture attempt timed out for order {paypal_order_id}. Initiating reconciliation probe.")
                    try:
                        return await self.reconcile_timed_out_capture(
                            db=db,
                            proposal_id=proposal_id,
                            paypal_order_id=paypal_order_id,
                        )
                    except ConfirmedUncapturedOrderError:
                        tx.reconciliation_status = "RECONCILIATION_REQUIRED"
                        tx.paypal_status = "RECONCILIATION_REQUIRED"
                        tx.updated_at = datetime.now(timezone.utc)
                        await db.commit()
                        raise PaymentCaptureError(
                            f"Capture timed out for order '{paypal_order_id}' and order remains uncaptured. Transaction marked RECONCILIATION_REQUIRED."
                        )
                else:
                    fail_stmt = (
                        update(Transaction)
                        .where(Transaction.id == tx.id)
                        .values(paypal_status="FAILED", updated_at=datetime.now(timezone.utc))
                    )
                    await db.execute(fail_stmt)
                    await db.commit()
                    raise

            capture_status = capture_resp.get("status")
            capture_id = None
            purchase_units = capture_resp.get("purchase_units", [])
            if purchase_units:
                payments = purchase_units[0].get("payments", {})
                captures = payments.get("captures", [])
                if captures:
                    capture_id = captures[0].get("id")
                    capture_status = captures[0].get("status", capture_status)

            if not capture_id:
                capture_id = f"PAYPAL-CAP-{uuid.uuid4().hex[:12].upper()}"

            # Check if simulation mode
            is_sim = self.client.is_simulated or capture_resp.get("is_simulated", False)

            if is_sim:
                # SIMULATED MODE: Never mark as PAID!
                # Keep state as PAYMENT_PENDING, set paypal_status to "SIMULATED", is_simulated to True
                tx.paypal_capture_id = capture_id
                tx.paypal_status = "SIMULATED"
                tx.is_simulated = True
                tx.updated_at = now_utc
                # Finding 2 & 3: Release budget reservation safely, do not touch genuine monthly spend
                await release_care_budget(db, proposal.id, is_simulated=True)

                audit = AuditEvent(
                    timestamp=now_utc,
                    event_type=AuditEventType.PAYPAL_ORDER_CAPTURED,
                    actor=proposal.user_id,
                    role="PAYPAL_GATEWAY",
                    module=to_str(proposal.module),
                    summary=f"Simulated payment completed (OFFLINE DEMO - NO REAL PAYPAL TRANSACTION) for Order #{paypal_order_id}.",
                    entity_id=proposal.id,
                    fingerprint=authoritative_hash,
                    previous_state=to_str(proposal_state),
                    new_state=to_str(TransactionLifecycleState.PAYMENT_PENDING),
                    details={
                        "paypal_order_id": paypal_order_id,
                        "paypal_capture_id": capture_id,
                        "capture_status": "SIMULATED",
                        "total_cents": proposal.total_cents,
                        "currency": proposal.currency,
                        "is_simulated": True,
                        "notice": "SIMULATED — NO PAYPAL TRANSACTION CREATED OR FUNDS CAPTURED",
                    },
                    severity=SeverityLevel.INFO,
                )
                db.add(audit)
                await db.commit()
                await db.refresh(tx)

                return PayPalCaptureResponse(
                    capture_id=capture_id,
                    order_id=paypal_order_id,
                    status="SIMULATED",
                    total_cents=proposal.total_cents,
                    currency=proposal.currency,
                    proposal_id=proposal.id,
                    fingerprint_sha256=authoritative_hash,
                    is_simulated=True,
                    reconciliation_status=tx.reconciliation_status,
                    captured_at=now_utc,
                )

            # REAL PROVIDER MODE
            if capture_status != "COMPLETED":
                raise PaymentCaptureError(f"PayPal capture did not complete successfully. Status: {capture_status}")

            # Dynamic lifecycle validation for real provider capture
            try:
                validate_transition(proposal_state, TransactionLifecycleState.PAID)
                validate_transition(tx_state, TransactionLifecycleState.PAID)
                proposal.state = TransactionLifecycleState.PAID
                tx.state = TransactionLifecycleState.PAID
            except IllegalStateTransitionError as exc:
                raise PaymentCaptureError(str(exc))

            tx.paypal_capture_id = capture_id
            tx.paypal_status = "COMPLETED"
            tx.is_simulated = False
            tx.updated_at = now_utc
            # Finding 2 & 3: Real COMPLETED capture consumes held budget reservation and increments monthly_spent_cents once
            await consume_care_budget(db, proposal.id)

            audit = AuditEvent(
                timestamp=now_utc,
                event_type=AuditEventType.PAYPAL_ORDER_CAPTURED,
                actor=proposal.user_id,
                role="PAYPAL_GATEWAY",
                module=to_str(proposal.module),
                summary=f"Captured verified PayPal Sandbox payment #{capture_id} for Order #{paypal_order_id} (Proposal #{proposal.id}).",
                entity_id=proposal.id,
                fingerprint=authoritative_hash,
                previous_state=to_str(proposal_state),
                new_state=to_str(TransactionLifecycleState.PAID),
                details={
                    "paypal_order_id": paypal_order_id,
                    "paypal_capture_id": capture_id,
                    "capture_status": capture_status,
                    "total_cents": proposal.total_cents,
                    "currency": proposal.currency,
                    "is_simulated": False,
                },
                severity=SeverityLevel.SUCCESS,
            )
            db.add(audit)
            await db.commit()
            await db.refresh(tx)

            return PayPalCaptureResponse(
                capture_id=capture_id,
                order_id=paypal_order_id,
                status="COMPLETED",
                total_cents=proposal.total_cents,
                currency=proposal.currency,
                proposal_id=proposal.id,
                fingerprint_sha256=authoritative_hash,
                is_simulated=False,
                reconciliation_status=tx.reconciliation_status,
                captured_at=now_utc,
            )

    async def get_payment_details(self, db: AsyncSession, proposal_id: str) -> PaymentDetailResponse:
        """Retrieves authoritative payment attempt record."""
        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        tx = tx_res.scalars().first()

        if not tx:
            raise PaymentVerificationError(f"No transaction record found for proposal '{proposal_id}'.")

        return PaymentDetailResponse(
            transaction_id=tx.id,
            proposal_id=tx.proposal_id,
            user_id=tx.user_id,
            state=to_str(tx.state),
            total_cents=tx.total_cents,
            currency=tx.currency,
            paypal_order_id=tx.paypal_order_id,
            paypal_capture_id=tx.paypal_capture_id,
            paypal_status=tx.paypal_status,
            paypal_approval_url=tx.paypal_approval_url,
            fingerprint_sha256=tx.fingerprint_sha256,
            is_simulated=tx.is_simulated,
            reconciliation_status=tx.reconciliation_status,
            capture_idempotency_key=tx.capture_idempotency_key,
            created_at=tx.created_at,
            updated_at=tx.updated_at,
        )

    async def cancel_payment_attempt(self, db: AsyncSession, proposal_id: str) -> None:
        """Safely transitions pending payment to CANCELLED."""
        query = select(PurchaseProposal).where(PurchaseProposal.id == proposal_id)
        res = await db.execute(query)
        proposal = res.scalar_one_or_none()

        if not proposal:
            raise PaymentVerificationError(f"Proposal '{proposal_id}' not found.")

        current_state = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )

        validate_transition(current_state, TransactionLifecycleState.CANCELLED)
        proposal.state = TransactionLifecycleState.CANCELLED

        tx_query = select(Transaction).where(Transaction.proposal_id == proposal_id)
        tx_res = await db.execute(tx_query)
        tx = tx_res.scalars().first()
        if tx:
            tx.state = TransactionLifecycleState.CANCELLED

        # Defect 2: Central, idempotent reservation release on payment cancellation
        await release_care_budget(db, proposal_id, is_simulated=False)

        now_utc = datetime.now(timezone.utc)
        audit = AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.STATE_TRANSITION,
            actor=proposal.user_id,
            role="USER",
            module=to_str(proposal.module),
            summary=f"Cancelled payment attempt for proposal #{proposal.id}.",
            entity_id=proposal.id,
            previous_state=to_str(current_state),
            new_state=to_str(TransactionLifecycleState.CANCELLED),
            severity=SeverityLevel.INFO,
        )
        db.add(audit)
        await db.commit()
