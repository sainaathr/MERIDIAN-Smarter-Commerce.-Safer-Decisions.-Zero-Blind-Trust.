"""
MERIDIAN — Concurrency-Safe CARE Budget Reservation & Consumption Engine
Enforces:
1. Atomic pre-order budget reservation:
   monthly_spent_cents + reserved_cents + requested_cents <= monthly_budget_cents
2. Idempotent consumption (HELD -> CONSUMED) on verified payment capture.
3. Safe release (HELD -> RELEASED) on payment failure, cancellation, or simulated checkout.
4. Total isolation between genuine monthly expenditure and simulated demo metrics.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ReservationStatus, TransactionLifecycleState, AuditEventType, SeverityLevel
from app.models.entities import CareProfile, ConsentPolicy, PurchaseProposal, BudgetReservation, Approval, AuditEvent, TransactionFingerprint, Transaction


async def reserve_care_budget(
    db: AsyncSession,
    proposal: PurchaseProposal,
    care_profile: CareProfile,
    consent_policy: Optional[ConsentPolicy] = None,
) -> Tuple[bool, Optional[BudgetReservation], str]:
    """
    Atomically reserves budget for a CARE proposal prior to payment authorization.
    Enforces that (monthly_spent + reserved + requested) <= monthly_budget.
    Guarantees no race conditions across concurrent reorder requests.
    """
    # 1. Check for existing reservation on this proposal (idempotency guard)
    existing = await db.scalar(
        select(BudgetReservation).where(BudgetReservation.proposal_id == proposal.id)
    )
    if existing:
        if existing.status == ReservationStatus.HELD:
            return True, existing, "Reservation already held for this proposal."
        return False, None, f"Proposal already has budget reservation in status {existing.status.value}."

    amount_cents = proposal.total_cents
    if amount_cents <= 0:
        return False, None, "Invalid requested reservation amount: must be positive."

    # 2. Database-atomic conditional update on CareProfile
    # If another concurrent request already committed and increased reserved_cents,
    # the WHERE condition fails and rowcount is 0.
    stmt = (
        update(CareProfile)
        .where(
            CareProfile.id == care_profile.id,
            (CareProfile.monthly_spent_cents + CareProfile.reserved_cents + amount_cents)
            <= CareProfile.monthly_budget_cents,
        )
        .values(reserved_cents=CareProfile.reserved_cents + amount_cents)
    )
    res = await db.execute(stmt)
    if res.rowcount == 0:
        return (
            False,
            None,
            f"BUDGET RESERVATION DENIAL: Requested order of ${amount_cents / 100:.2f} would cause "
            f"combined spend and active reservations (${(care_profile.monthly_spent_cents + care_profile.reserved_cents + amount_cents) / 100:.2f}) "
            f"to exceed monthly budget ceiling (${care_profile.monthly_budget_cents / 100:.2f}).",
        )

    # 3. Synchronize ConsentPolicy reserved_cents if policy provided
    if consent_policy:
        await db.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.id == consent_policy.id)
            .values(reserved_cents=ConsentPolicy.reserved_cents + amount_cents)
        )

    # 4. Create authoritative BudgetReservation record
    reservation = BudgetReservation(
        id=f"resv-{uuid.uuid4().hex[:12]}",
        proposal_id=proposal.id,
        care_profile_id=care_profile.id,
        consent_policy_id=consent_policy.id if consent_policy else None,
        amount_cents=amount_cents,
        currency=proposal.currency,
        status=ReservationStatus.HELD,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(reservation)
    await db.flush()
    return True, reservation, "Budget reservation atomically held."


async def consume_care_budget(db: AsyncSession, proposal_id: str) -> bool:
    """
    Atomically transitions a held reservation to CONSUMED upon verified real PayPal capture.
    Idempotent: returns False if reservation is not in HELD status (already consumed or released),
    preventing double-counting across concurrent or repeated reconciliation calls.
    """
    stmt = (
        update(BudgetReservation)
        .where(
            BudgetReservation.proposal_id == proposal_id,
            BudgetReservation.status == ReservationStatus.HELD,
        )
        .values(
            status=ReservationStatus.CONSUMED,
            updated_at=datetime.now(timezone.utc),
        )
    )
    res = await db.execute(stmt)
    if res.rowcount == 0:
        # Not held (either not found, already consumed, or released)
        return False

    reservation = await db.scalar(
        select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id)
    )
    if not reservation:
        return False

    # Atomically decrement reserved_cents and increment genuine monthly_spent_cents
    await db.execute(
        update(CareProfile)
        .where(CareProfile.id == reservation.care_profile_id)
        .values(
            reserved_cents=func.max(0, CareProfile.reserved_cents - reservation.amount_cents),
            monthly_spent_cents=CareProfile.monthly_spent_cents + reservation.amount_cents,
        )
    )

    if reservation.consent_policy_id:
        await db.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.id == reservation.consent_policy_id)
            .values(
                reserved_cents=func.max(0, ConsentPolicy.reserved_cents - reservation.amount_cents),
                monthly_spent_cents=ConsentPolicy.monthly_spent_cents + reservation.amount_cents,
            )
        )

    return True


# Explicit whitelist of transaction reconciliation statuses that conclusively prove provider non-creation
SAFE_NON_CREATION_RECONCILIATION_STATUSES = {"CREATION_FAILED"}

async def is_safe_to_release_care_reservation(
    db: AsyncSession,
    proposal_id: str,
) -> Tuple[bool, str]:
    """
    Central fail-closed safety predicate: determines whether provider-side non-creation
    or definitive cancellation has been conclusively established before releasing a CARE budget reservation.

    Fails closed on:
    - Missing or null reconciliation status
    - Unrecognised or unknown reconciliation status
    - Any ambiguous in-flight or recovery status
    - Any transaction record with an assigned PayPal order ID
    - Inconsistent or malformed transaction records
    - Merely missing `first_dispatched_at` (never treated as proof of non-dispatch)

    Returns:
        (is_safe: bool, reason: str)
    """
    tx = await db.scalar(select(Transaction).where(Transaction.proposal_id == proposal_id))
    if not tx:
        # Genuinely undispatched proposal: no payment transaction was ever created
        return True, "No payment transaction was ever created for this proposal."

    # If an external PayPal order ID was assigned, order exists externally
    if tx.paypal_order_id:
        return False, f"Provider order '{tx.paypal_order_id}' was assigned; cannot release reservation without verified provider cancellation."

    # Null, empty, or unrecognised reconciliation status must strictly fail closed
    status = tx.reconciliation_status
    if not status or not isinstance(status, str):
        return False, "Transaction reconciliation status is null or malformed; cannot prove provider non-creation."

    status = status.strip()
    if not status:
        return False, "Transaction reconciliation status is empty; cannot prove provider non-creation."

    # Explicit whitelist check: ONLY CREATION_FAILED proves non-creation
    if status in SAFE_NON_CREATION_RECONCILIATION_STATUSES:
        # Consistency verification: state must not be PAID, and order ID must be None
        tx_state = (
            tx.state
            if isinstance(tx.state, TransactionLifecycleState)
            else (TransactionLifecycleState(str(tx.state)) if tx.state else None)
        )
        if tx_state == TransactionLifecycleState.PAID:
            return False, "Transaction record is inconsistent (CREATION_FAILED with PAID lifecycle state); failing closed."
        return True, "Definitive provider non-creation verified (CREATION_FAILED)."

    # Explicit check for known ambiguous / in-flight statuses
    known_ambiguous_statuses = {
        "ORDER_CREATION_IN_PROGRESS",
        "ORDER_CREATION_RECOVERING",
        "RECONCILIATION_REQUIRED",
        "MANUAL_REVIEW_REQUIRED",
        "CAPTURING",
        "ORDER_CREATED",
        "RECOVERY_BLOCKED",
    }
    if status in known_ambiguous_statuses:
        return False, f"Transaction is in ambiguous or unverified provider status '{status}'; retaining reservation."

    # Fail closed on any other unknown or unexpected status
    return False, f"Transaction is in unrecognised reconciliation status '{status}'; failing closed."


async def release_care_budget(
    db: AsyncSession,
    proposal_id: str,
    is_simulated: bool = False,
    force_safe: bool = False,
) -> bool:
    """
    Safely releases a held budget reservation upon payment failure, cancellation,
    proposal rejection, or mock simulated payment.
    If is_simulated is True, increments simulated_monthly_spent_cents rather than genuine spend.
    Enforces central safety predicate: refuses release if the proposal has an ambiguous
    in-flight transaction with the payment provider.
    """
    if not is_simulated and not force_safe:
        is_safe, reason = await is_safe_to_release_care_reservation(db, proposal_id)
        if not is_safe:
            return False

    stmt = (
        update(BudgetReservation)
        .where(
            BudgetReservation.proposal_id == proposal_id,
            BudgetReservation.status == ReservationStatus.HELD,
        )
        .values(
            status=ReservationStatus.RELEASED,
            updated_at=datetime.now(timezone.utc),
        )
    )
    res = await db.execute(stmt)
    if res.rowcount == 0:
        return False

    reservation = await db.scalar(
        select(BudgetReservation).where(BudgetReservation.proposal_id == proposal_id)
    )
    if not reservation:
        return False

    # Release the reserved cents from genuine accounting
    await db.execute(
        update(CareProfile)
        .where(CareProfile.id == reservation.care_profile_id)
        .values(
            reserved_cents=func.max(0, CareProfile.reserved_cents - reservation.amount_cents)
        )
    )

    if reservation.consent_policy_id:
        await db.execute(
            update(ConsentPolicy)
            .where(ConsentPolicy.id == reservation.consent_policy_id)
            .values(
                reserved_cents=func.max(0, ConsentPolicy.reserved_cents - reservation.amount_cents)
            )
        )

    # If this was a simulated mock capture, update isolated demo statistics
    if is_simulated:
        await db.execute(
            update(CareProfile)
            .where(CareProfile.id == reservation.care_profile_id)
            .values(
                simulated_monthly_spent_cents=CareProfile.simulated_monthly_spent_cents + reservation.amount_cents
            )
        )
        if reservation.consent_policy_id:
            await db.execute(
                update(ConsentPolicy)
                .where(ConsentPolicy.id == reservation.consent_policy_id)
                .values(
                    simulated_monthly_spent_cents=ConsentPolicy.simulated_monthly_spent_cents + reservation.amount_cents
                )
            )

    return True


async def process_expired_reservations_and_proposals(
    db: AsyncSession,
    care_profile_id: Optional[str] = None,
) -> int:
    """
    Deterministic expiry-processing service.
    Finds HELD budget reservations whose proposals, approvals, or consent policies have expired
    or reached terminal non-payment states, and safely releases them.
    Invoked deterministically before budget evaluations and proposal access paths.
    Returns the count of reservations released.
    """
    now_utc = datetime.now(timezone.utc)
    query = (
        select(BudgetReservation)
        .where(BudgetReservation.status == ReservationStatus.HELD)
    )
    if care_profile_id:
        query = query.where(BudgetReservation.care_profile_id == care_profile_id)

    res = await db.execute(query)
    held_reservations = res.scalars().all()
    released_count = 0

    for r in held_reservations:
        proposal = await db.get(PurchaseProposal, r.proposal_id)
        if not proposal:
            # Inconsistent or deleted proposal: safely release reservation
            await release_care_budget(db, r.proposal_id, is_simulated=False)
            released_count += 1
            continue

        p_state = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )

        should_release = False
        expiry_reason = None

        # 1. Proposal already in a terminal non-payment state
        if p_state in (
            TransactionLifecycleState.REJECTED,
            TransactionLifecycleState.CANCELLED,
            TransactionLifecycleState.EXPIRED,
            TransactionLifecycleState.FAILED,
        ):
            should_release = True
            expiry_reason = f"Proposal is in terminal non-payment state '{p_state.value}'"

        # 2. Check for expired Approval
        if not should_release:
            fp = await db.scalar(
                select(TransactionFingerprint).where(TransactionFingerprint.proposal_id == r.proposal_id)
            )
            if fp:
                appr = await db.scalar(
                    select(Approval).where(
                        Approval.fingerprint_hash == fp.sha256_hash,
                        Approval.is_revoked == False,
                    )
                )
                if appr:
                    appr_exp = appr.expires_at
                    if appr_exp and appr_exp.tzinfo is None:
                        appr_exp = appr_exp.replace(tzinfo=timezone.utc)
                    if appr_exp and appr_exp < now_utc:
                        should_release = True
                        expiry_reason = f"Associated approval expired at {appr_exp.isoformat()}"
                        if p_state in (TransactionLifecycleState.AWAITING_APPROVAL, TransactionLifecycleState.APPROVED):
                            proposal.state = TransactionLifecycleState.EXPIRED
                            proposal.updated_at = now_utc

        # 3. Check for revoked or expired ConsentPolicy
        if not should_release and r.consent_policy_id:
            policy = await db.get(ConsentPolicy, r.consent_policy_id)
            if policy:
                if policy.is_revoked:
                    should_release = True
                    expiry_reason = f"Consent policy #{policy.id} was revoked"
                elif policy.valid_until:
                    pol_valid = policy.valid_until
                    if pol_valid.tzinfo is None:
                        pol_valid = pol_valid.replace(tzinfo=timezone.utc)
                    if pol_valid < now_utc:
                        should_release = True
                        expiry_reason = f"Consent policy #{policy.id} expired at {pol_valid.isoformat()}"

        # 4. Check for proposal TTL in AWAITING_APPROVAL (older than 24 hours without approval)
        if not should_release and p_state == TransactionLifecycleState.AWAITING_APPROVAL:
            p_created = proposal.created_at
            if p_created and p_created.tzinfo is None:
                p_created = p_created.replace(tzinfo=timezone.utc)
            if p_created and (now_utc - p_created).total_seconds() > 86400:
                should_release = True
                expiry_reason = f"Proposal in AWAITING_APPROVAL exceeded 24-hour TTL (created {p_created.isoformat()})"
                proposal.state = TransactionLifecycleState.EXPIRED
                proposal.updated_at = now_utc

        if should_release:
            is_safe, safe_reason = await is_safe_to_release_care_reservation(db, r.proposal_id)
            if not is_safe:
                # Ambiguous provider state: preserve reservation in HELD status
                # If transaction is in ORDER_CREATION_IN_PROGRESS or RECONCILIATION_REQUIRED, move to MANUAL_REVIEW_REQUIRED
                tx = await db.scalar(select(Transaction).where(Transaction.proposal_id == r.proposal_id))
                if tx and tx.reconciliation_status in ("ORDER_CREATION_IN_PROGRESS", "ORDER_CREATION_RECOVERING", "RECONCILIATION_REQUIRED"):
                    tx.reconciliation_status = "MANUAL_REVIEW_REQUIRED"
                    tx.updated_at = now_utc
                    db.add(
                        AuditEvent(
                            timestamp=now_utc,
                            event_type=AuditEventType.STATE_TRANSITION,
                            actor="Deterministic Expiry Processor",
                            role="SYSTEM",
                            module="CARE",
                            summary=f"Held reservation for Proposal #{r.proposal_id} preserved: {expiry_reason}, but {safe_reason}",
                            entity_id=r.proposal_id,
                            previous_state="HELD",
                            new_state="MANUAL_REVIEW_REQUIRED",
                            details={"care_profile_id": r.care_profile_id, "amount_cents": r.amount_cents, "reason": expiry_reason, "safe_reason": safe_reason},
                            severity=SeverityLevel.ALERT,
                        )
                    )
                continue

            did_release = await release_care_budget(db, r.proposal_id, is_simulated=False)
            if did_release:
                released_count += 1
                db.add(
                    AuditEvent(
                        timestamp=now_utc,
                        event_type=AuditEventType.STATE_TRANSITION,
                        actor="Deterministic Expiry Processor",
                        role="SYSTEM",
                        module="CARE",
                        summary=f"Released held budget reservation (${r.amount_cents / 100:.2f}) for Proposal #{r.proposal_id}: {expiry_reason}",
                        entity_id=r.proposal_id,
                        previous_state="HELD",
                        new_state="RELEASED",
                        details={"care_profile_id": r.care_profile_id, "amount_cents": r.amount_cents, "reason": expiry_reason},
                        severity=SeverityLevel.INFO,
                    )
                )

    if released_count > 0:
        await db.commit()

    return released_count
