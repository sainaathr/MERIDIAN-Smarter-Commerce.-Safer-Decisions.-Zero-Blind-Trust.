from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.entities import (
    CareProfile,
    GuardianRelationship,
    ConsentPolicy,
    PurchaseProposal,
    ProposalItem,
    TransactionFingerprint,
    Approval,
    PolicyEvaluationRecord,
    AuditEvent,
    ReorderSchedule,
    Transaction,
)
from app.models.enums import (
    ApprovalMode,
    ApprovalType,
    ModuleScope,
    TransactionLifecycleState,
    PolicyDecision,
    AuditEventType,
    SeverityLevel,
)
from app.engine.fingerprint import compute_transaction_fingerprint
from app.engine.policy import evaluate_transaction_policy
from app.engine.approval import create_policy_automatic_approval
from app.engine.budget import reserve_care_budget, release_care_budget, process_expired_reservations_and_proposals
from app.payments.paypal_service import PayPalService
from app.schemas.domain import (
    CareProfileResponse,
    CareItemResponse,
    GuardianResponse,
    CareModeUpdateRequest,
    CareReorderRequest,
    CareReorderResponse,
    ProposalResponse,
    ProposalItemResponse,
)

router = APIRouter()

def to_str(val) -> str:
    if val is None:
        return ""
    return val.value if hasattr(val, "value") else str(val)

def to_proposal_response(
    p: PurchaseProposal,
    tx: Optional[Transaction] = None,
    ev: Optional[PolicyEvaluationRecord] = None,
    fingerprint_hash: Optional[str] = None,
) -> ProposalResponse:
    fp = fingerprint_hash
    if not fp and hasattr(p, "fingerprint") and p.fingerprint:
        fp = p.fingerprint.sha256_hash
    return ProposalResponse(
        id=p.id,
        user_id=p.user_id,
        care_profile_id=p.care_profile_id,
        module=p.module,
        title=p.title,
        merchant_id=p.merchant_id,
        merchant_name=p.merchant_name,
        items=[
            ProposalItemResponse(
                id=it.id,
                product_id=it.product_id,
                name=it.name,
                sku=it.sku,
                category=it.category,
                quantity=it.quantity,
                unit_price_cents=it.unit_price_cents,
                total_cents=it.total_cents,
            )
            for it in p.items
        ],
        subtotal_cents=p.subtotal_cents,
        tax_cents=p.tax_cents,
        shipping_cents=p.shipping_cents,
        total_cents=p.total_cents,
        currency=p.currency,
        ai_reasoning=p.ai_reasoning,
        state=p.state,
        version=p.version,
        created_at=p.created_at,
        fingerprint_sha256=fp,
        policy_decision=to_str(ev.decision) if ev else None,
        paypal_order_id=tx.paypal_order_id if tx else None,
        paypal_capture_id=tx.paypal_capture_id if tx else None,
        paypal_status=tx.paypal_status if tx else None,
        is_simulated=tx.is_simulated if tx else None,
        updated_at=tx.updated_at if tx else p.updated_at,
    )

async def load_proposal_full(db: AsyncSession, proposal_id: str) -> PurchaseProposal:
    res = await db.execute(
        select(PurchaseProposal)
        .where(PurchaseProposal.id == proposal_id)
        .options(
            selectinload(PurchaseProposal.items),
            selectinload(PurchaseProposal.fingerprint),
        )
    )
    return res.scalar_one()

def to_care_profile_response(cp: CareProfile) -> CareProfileResponse:
    guardians_resp = []
    for g in cp.guardians:
        guardians_resp.append(
            GuardianResponse(
                id=g.id,
                name=g.guardian_user.full_name if g.guardian_user else "Care Guardian",
                role=g.relationship_type,
                email=g.guardian_user.email if g.guardian_user else "",
                can_approve=g.can_approve,
                max_approval_amount=round(g.max_approval_amount_cents / 100.0, 2),
            )
        )

    items_resp = []
    for s in cp.reorder_schedules:
        items_resp.append(
            CareItemResponse(
                id=s.id,
                name=s.product_name,
                category=s.category,
                frequency=f"Every {s.frequency_days} days",
                days_remaining=s.days_remaining,
                estimated_cost=round(s.unit_cost_cents / 100.0, 2),
                merchant=s.preferred_merchant,
                auto_reorder_eligible=s.auto_reorder_eligible,
                last_ordered=s.last_ordered_at.strftime("%Y-%m-%d") if s.last_ordered_at else "2026-10-15",
            )
        )

    mode_val = cp.approval_mode.value if hasattr(cp.approval_mode, "value") else str(cp.approval_mode)
    if mode_val in (ApprovalMode.CONTROLLED_AUTO.value, "MODE_B_CONTROLLED_AUTO", "CONTROLLED_AUTO"):
        mode_str = "MODE_B_CONTROLLED_AUTO"
    else:
        mode_str = "MODE_A_EVERY_ORDER"

    return CareProfileResponse(
        id=cp.id,
        user_id=cp.user_id,
        recipient_name=cp.recipient_name,
        relationship="Mother (Primary Care Recipient)",
        approval_mode=mode_str,
        monthly_budget=round(cp.monthly_budget_cents / 100.0, 2),
        monthly_spent=round(cp.monthly_spent_cents / 100.0, 2),
        reserved_amount=round(getattr(cp, "reserved_cents", 0) / 100.0, 2),
        simulated_monthly_spent=round(getattr(cp, "simulated_monthly_spent_cents", 0) / 100.0, 2),
        currency=cp.currency,
        guardians=guardians_resp,
        monitored_items=items_resp,
    )

@router.get("/profile", response_model=CareProfileResponse)
async def get_care_profile(
    care_profile_id: Optional[str] = "care-eleanor-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Authoritative query for CARE recipient profile, authorized guardian circle,
    and monitored replenishment essentials.
    """
    profile_id = care_profile_id or "care-eleanor-01"
    await process_expired_reservations_and_proposals(db, care_profile_id=profile_id)
    query = (
        select(CareProfile)
        .where(CareProfile.id == profile_id)
        .options(
            selectinload(CareProfile.guardians).selectinload(GuardianRelationship.guardian_user),
            selectinload(CareProfile.reorder_schedules),
        )
    )
    result = await db.execute(query)
    care_profile = result.scalar_one_or_none()
    if not care_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Care profile '{profile_id}' not found.",
        )

    return to_care_profile_response(care_profile)

@router.post("/mode", response_model=CareProfileResponse)
async def update_care_mode(req: CareModeUpdateRequest, db: AsyncSession = Depends(get_db)):
    """
    Atomically toggles Care approval mode and synchronizes both CareProfile and
    active ConsentPolicy records between Mode A (EXPLICIT_ALWAYS) and Mode B (CONTROLLED_AUTO).
    """
    mode_str = req.mode.upper()
    if mode_str in ("MODE_B_CONTROLLED_AUTO", "CONTROLLED_AUTO"):
        target_mode = ApprovalMode.CONTROLLED_AUTO
    elif mode_str in ("MODE_A_EVERY_ORDER", "EXPLICIT_ALWAYS"):
        target_mode = ApprovalMode.EXPLICIT_ALWAYS
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid mode '{req.mode}'. Must be 'MODE_A_EVERY_ORDER' or 'MODE_B_CONTROLLED_AUTO'.",
        )

    profile_id = req.care_profile_id or "care-eleanor-01"
    query = (
        select(CareProfile)
        .where(CareProfile.id == profile_id)
        .options(
            selectinload(CareProfile.guardians).selectinload(GuardianRelationship.guardian_user),
            selectinload(CareProfile.reorder_schedules),
        )
    )
    result = await db.execute(query)
    care_profile = result.scalar_one_or_none()
    if not care_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Care profile '{profile_id}' not found.",
        )

    care_profile.approval_mode = target_mode

    # Synchronize corresponding ConsentPolicy for CARE scope
    policy_query = (
        select(ConsentPolicy)
        .where(
            ConsentPolicy.user_id == care_profile.user_id,
            ConsentPolicy.scope == ModuleScope.CARE,
            ConsentPolicy.is_revoked == False,
        )
    )
    pol_res = await db.execute(policy_query)
    consent_policy = pol_res.scalars().first()
    if consent_policy:
        consent_policy.approval_mode = target_mode
        consent_policy.policy_version += 1

    # Emit audit event
    now_utc = datetime.now(timezone.utc)
    audit = AuditEvent(
        timestamp=now_utc,
        event_type=AuditEventType.STATE_TRANSITION,
        actor="usr-alex-01",
        role="PRIMARY_GUARDIAN",
        module="CARE",
        summary=f"Care profile approval mode updated to {target_mode.value}",
        entity_id=care_profile.id,
        details={
            "care_profile_id": care_profile.id,
            "new_mode": target_mode.value,
            "consent_policy_id": consent_policy.id if consent_policy else None,
        },
        severity=SeverityLevel.INFO,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(care_profile)

    return to_care_profile_response(care_profile)

@router.post("/reorder", response_model=CareReorderResponse)
async def trigger_care_reorder(req: CareReorderRequest, db: AsyncSession = Depends(get_db)):
    """
    Executes Protected Autonomy reordering workflow:
    1. Validates reorder schedule exists and meets medical safety eligibility.
    2. Builds authoritative PurchaseProposal with canonical line items and SHA-256 fingerprint.
    3. Evaluates against Universal Trust Engine policy:
       - If DENY: transitions to REJECTED, blocks order creation.
       - If Mode A (REQUIRE_APPROVAL): transitions to AWAITING_APPROVAL for explicit human confirmation.
       - If Mode B (ALLOW): transitions to APPROVED with POLICY_AUTOMATIC approval, then
         automatically creates PayPal Sandbox order in PAYMENT_PENDING state.
    """
    now_utc = datetime.now(timezone.utc)
    await process_expired_reservations_and_proposals(db, care_profile_id=req.care_profile_id)

    # 1. Fetch schedule
    sched_res = await db.execute(select(ReorderSchedule).where(ReorderSchedule.id == req.schedule_id))
    schedule = sched_res.scalar_one_or_none()
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reorder schedule '{req.schedule_id}' not found.",
        )

    # 2. Invariant: Medical safety rules
    if not schedule.auto_reorder_eligible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Safety rule failure: Item is not eligible for autonomous reorder under authorized care plan.",
        )

    # 3. Fetch CareProfile
    profile_id = req.care_profile_id or schedule.care_profile_id
    care_res = await db.execute(
        select(CareProfile)
        .where(CareProfile.id == profile_id)
        .options(
            selectinload(CareProfile.guardians).selectinload(GuardianRelationship.guardian_user),
        )
    )
    care_profile = care_res.scalar_one_or_none()
    if not care_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Care profile '{profile_id}' not found.",
        )

    # 4. Fetch active ConsentPolicy for CARE scope
    policy_res = await db.execute(
        select(ConsentPolicy)
        .where(
            ConsentPolicy.user_id == care_profile.user_id,
            ConsentPolicy.scope == ModuleScope.CARE,
            ConsentPolicy.is_revoked == False,
        )
    )
    consent_policy = policy_res.scalars().first()
    if not consent_policy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active consent policy found for Care module.",
        )

    # 5. Build proposal with integer minor units (zero floating-point math)
    total_cents = schedule.unit_cost_cents
    merchant_id = "m-carerx-02" if "carerx" in schedule.preferred_merchant.lower() else f"m-{schedule.preferred_merchant.lower().replace(' ', '-')[:25]}"

    proposal = PurchaseProposal(
        user_id=care_profile.user_id,
        care_profile_id=care_profile.id,
        module=ModuleScope.CARE,
        title=f"Care Replenishment: {schedule.product_name}",
        merchant_id=merchant_id,
        merchant_name=schedule.preferred_merchant,
        subtotal_cents=total_cents,
        tax_cents=0,
        shipping_cents=0,
        total_cents=total_cents,
        currency=care_profile.currency,
        ai_reasoning=f"Autonomous replenishment triggered for {schedule.product_name} ({schedule.days_remaining} days remaining) under active care schedule.",
        state=TransactionLifecycleState.PROPOSED,
    )
    db.add(proposal)
    await db.flush()

    # 6. Add proposal item
    prod_id = f"prod-{schedule.id}"
    item = ProposalItem(
        proposal_id=proposal.id,
        product_id=prod_id,
        name=schedule.product_name,
        sku=f"SKU-{schedule.id}",
        category=schedule.category,
        quantity=1,
        unit_price_cents=schedule.unit_cost_cents,
        total_cents=total_cents,
    )
    db.add(item)
    await db.flush()

    # 7. Compute cryptographic SHA-256 fingerprint
    items_for_fp = [
        {
            "product_id": prod_id,
            "name": schedule.product_name,
            "sku": f"SKU-{schedule.id}",
            "category": schedule.category,
            "quantity": 1,
            "unit_price_cents": schedule.unit_cost_cents,
            "total_cents": total_cents,
        }
    ]
    canonical_payload, sha256_hash = compute_transaction_fingerprint(
        user_id=care_profile.user_id,
        module=to_str(ModuleScope.CARE),
        merchant_id=merchant_id,
        merchant_name=schedule.preferred_merchant,
        items=items_for_fp,
        total_cents=total_cents,
        currency=care_profile.currency,
        care_profile_id=care_profile.id,
        subtotal_cents=total_cents,
        tax_cents=0,
        shipping_cents=0,
        policy_version=consent_policy.policy_version,
    )

    fingerprint_record = TransactionFingerprint(
        proposal_id=proposal.id,
        canonical_payload=canonical_payload,
        sha256_hash=sha256_hash,
        policy_version=consent_policy.policy_version,
        created_at=now_utc,
    )
    db.add(fingerprint_record)

    # Transition state to POLICY_CHECK
    proposal.state = TransactionLifecycleState.POLICY_CHECK
    proposal.version += 1
    proposal.updated_at = now_utc

    # Audit proposal creation and fingerprint
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.PROPOSAL_CREATED,
            actor=req.actor_id or "usr-alex-01",
            role="CARE_SYSTEM",
            module="CARE",
            summary=f"Created Care Replenishment Proposal #{proposal.id} for {schedule.product_name}",
            entity_id=proposal.id,
            details={"total_cents": total_cents, "schedule_id": schedule.id},
            severity=SeverityLevel.INFO,
        )
    )
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.FINGERPRINT_GENERATED,
            actor="Trust Engine Cryptographic Kernel",
            role="SYSTEM",
            module="CARE",
            summary=f"Bound canonical SHA-256 fingerprint: {sha256_hash[:16]}...",
            entity_id=proposal.id,
            fingerprint=sha256_hash,
            details={"policy_version": consent_policy.policy_version},
            severity=SeverityLevel.INFO,
        )
    )

    # 8. Evaluate policy through Universal Trust Engine
    policy_dict = {
        "max_per_order_cents": consent_policy.max_per_order_cents,
        "monthly_budget_cents": care_profile.monthly_budget_cents,
        "monthly_spent_cents": care_profile.monthly_spent_cents,
        "monthly_reserved_cents": care_profile.reserved_cents,
        "currency": consent_policy.currency,
        "allowed_merchants": consent_policy.allowed_merchants,
        "allowed_categories": consent_policy.allowed_categories,
        "approval_mode": consent_policy.approval_mode,
        "valid_until": consent_policy.valid_until,
        "is_revoked": consent_policy.is_revoked,
        "policy_version": consent_policy.policy_version,
        "requires_reorder_schedule": True,
    }
    reorder_sched_dict = {
        "id": schedule.id,
        "product_name": schedule.product_name,
        "is_active": schedule.auto_reorder_eligible,
        "frequency_days": schedule.frequency_days,
        "unit_cost_cents": schedule.unit_cost_cents,
    }
    baseline_prices = {prod_id: schedule.unit_cost_cents}

    guardian_dict = None
    if req.actor_id:
        for g in care_profile.guardians:
            if g.guardian_user_id == req.actor_id:
                guardian_dict = {
                    "relationship_type": g.relationship_type,
                    "can_approve": g.can_approve,
                    "max_approval_amount_cents": g.max_approval_amount_cents,
                    "is_active": g.is_active,
                }
                break

    eval_result = evaluate_transaction_policy(
        user_id=care_profile.user_id,
        module=ModuleScope.CARE,
        merchant_id=merchant_id,
        merchant_name=schedule.preferred_merchant,
        items=items_for_fp,
        total_cents=total_cents,
        currency=care_profile.currency,
        fingerprint_hash=sha256_hash,
        computed_fingerprint_hash=sha256_hash,
        policy=policy_dict,
        acting_user_id=req.actor_id or "usr-alex-01",
        guardian_delegation=guardian_dict,
        reorder_schedule=reorder_sched_dict,
        baseline_item_prices=baseline_prices,
    )

    # Record PolicyEvaluationRecord
    eval_record = PolicyEvaluationRecord(
        proposal_id=proposal.id,
        decision=eval_result.decision,
        rules_evaluated=eval_result.rules_evaluated,
        passed_checks=eval_result.passed_checks,
        failed_checks=eval_result.failed_checks,
        human_explanation=eval_result.human_explanation,
        fingerprint_hash=eval_result.fingerprint_hash,
        policy_version=eval_result.policy_version,
        created_at=eval_result.timestamp,
    )
    db.add(eval_record)

    db.add(
        AuditEvent(
            timestamp=eval_result.timestamp,
            event_type=AuditEventType.POLICY_EVALUATED,
            actor="Universal Trust Engine",
            role="SYSTEM",
            module="CARE",
            summary=f"Evaluated policy verdict: {to_str(eval_result.decision)}. {eval_result.human_explanation}",
            entity_id=proposal.id,
            decision=to_str(eval_result.decision),
            fingerprint=eval_result.fingerprint_hash,
            details={
                "rules_evaluated": eval_result.rules_evaluated,
                "passed": eval_result.passed_checks,
                "failed": eval_result.failed_checks,
            },
            severity=SeverityLevel.ALERT if eval_result.decision == PolicyDecision.DENY else SeverityLevel.INFO,
        )
    )

    # 9. Lifecycle Branching based on Trust Engine Decision
    if eval_result.decision == PolicyDecision.DENY:
        proposal.state = TransactionLifecycleState.REJECTED
        proposal.version += 1
        proposal.updated_at = eval_result.timestamp
        db.add(
            AuditEvent(
                timestamp=eval_result.timestamp,
                event_type=AuditEventType.STATE_TRANSITION,
                actor="Universal Trust Engine",
                role="ENGINE",
                module="CARE",
                summary=f"Proposal #{proposal.id} automatically transitioned to REJECTED due to policy denial: {eval_result.human_explanation}",
                entity_id=proposal.id,
                previous_state=to_str(TransactionLifecycleState.POLICY_CHECK),
                new_state=to_str(TransactionLifecycleState.REJECTED),
                decision="DENY",
                fingerprint=sha256_hash,
                details={"reason": eval_result.human_explanation},
                severity=SeverityLevel.ALERT,
            )
        )
        await db.commit()
        p_full = await load_proposal_full(db, proposal.id)
        return CareReorderResponse(
            proposal=to_proposal_response(p_full, ev=eval_record, fingerprint_hash=sha256_hash),
            requires_approval=False,
            decision=PolicyDecision.DENY,
            human_explanation=eval_result.human_explanation,
            order=None,
        )

    if eval_result.decision == PolicyDecision.REQUIRE_APPROVAL:
        # Finding 3: Atomically reserve budget before proposal enters AWAITING_APPROVAL
        resv_ok, resv, resv_msg = await reserve_care_budget(db, proposal, care_profile, consent_policy)
        if not resv_ok:
            proposal.state = TransactionLifecycleState.REJECTED
            proposal.version += 1
            proposal.updated_at = eval_result.timestamp
            db.add(
                AuditEvent(
                    timestamp=eval_result.timestamp,
                    event_type=AuditEventType.STATE_TRANSITION,
                    actor="Universal Trust Engine",
                    role="ENGINE",
                    module="CARE",
                    summary=f"Proposal #{proposal.id} automatically transitioned to REJECTED due to budget reservation failure: {resv_msg}",
                    entity_id=proposal.id,
                    previous_state=to_str(TransactionLifecycleState.POLICY_CHECK),
                    new_state=to_str(TransactionLifecycleState.REJECTED),
                    decision="DENY",
                    fingerprint=sha256_hash,
                    details={"reason": resv_msg},
                    severity=SeverityLevel.ALERT,
                )
            )
            await db.commit()
            p_full = await load_proposal_full(db, proposal.id)
            return CareReorderResponse(
                proposal=to_proposal_response(p_full, ev=eval_record, fingerprint_hash=sha256_hash),
                requires_approval=False,
                decision=PolicyDecision.DENY,
                human_explanation=resv_msg,
                order=None,
            )

        # Mode A: Staged proposal awaiting explicit guardian or recipient approval
        proposal.state = TransactionLifecycleState.AWAITING_APPROVAL
        proposal.version += 1
        proposal.updated_at = eval_result.timestamp
        db.add(
            AuditEvent(
                timestamp=eval_result.timestamp,
                event_type=AuditEventType.STATE_TRANSITION,
                actor="Universal Trust Engine",
                role="ENGINE",
                module="CARE",
                summary=f"Proposal #{proposal.id} transitioned to AWAITING_APPROVAL. Mode A requires explicit human authorization.",
                entity_id=proposal.id,
                previous_state=to_str(TransactionLifecycleState.POLICY_CHECK),
                new_state=to_str(TransactionLifecycleState.AWAITING_APPROVAL),
                decision="REQUIRE_APPROVAL",
                fingerprint=sha256_hash,
                details={"reason": eval_result.human_explanation},
                severity=SeverityLevel.INFO,
            )
        )
        await db.commit()
        p_full = await load_proposal_full(db, proposal.id)
        return CareReorderResponse(
            proposal=to_proposal_response(p_full, ev=eval_record, fingerprint_hash=sha256_hash),
            requires_approval=True,
            decision=PolicyDecision.REQUIRE_APPROVAL,
            human_explanation=eval_result.human_explanation,
            order=None,
        )

    # Finding 3: Atomically reserve budget before autonomous approval & order creation
    resv_ok, resv, resv_msg = await reserve_care_budget(db, proposal, care_profile, consent_policy)
    if not resv_ok:
        proposal.state = TransactionLifecycleState.REJECTED
        proposal.version += 1
        proposal.updated_at = eval_result.timestamp
        db.add(
            AuditEvent(
                timestamp=eval_result.timestamp,
                event_type=AuditEventType.STATE_TRANSITION,
                actor="Universal Trust Engine",
                role="ENGINE",
                module="CARE",
                summary=f"Proposal #{proposal.id} automatically transitioned to REJECTED due to budget reservation failure: {resv_msg}",
                entity_id=proposal.id,
                previous_state=to_str(TransactionLifecycleState.POLICY_CHECK),
                new_state=to_str(TransactionLifecycleState.REJECTED),
                decision="DENY",
                fingerprint=sha256_hash,
                details={"reason": resv_msg},
                severity=SeverityLevel.ALERT,
            )
        )
        await db.commit()
        p_full = await load_proposal_full(db, proposal.id)
        return CareReorderResponse(
            proposal=to_proposal_response(p_full, ev=eval_record, fingerprint_hash=sha256_hash),
            requires_approval=False,
            decision=PolicyDecision.DENY,
            human_explanation=resv_msg,
            order=None,
        )

    # Mode B: ALLOW — Automatic authorization & PayPal Sandbox order creation
    proposal.state = TransactionLifecycleState.APPROVED
    proposal.version += 1
    proposal.updated_at = eval_result.timestamp

    approval = create_policy_automatic_approval(
        fingerprint_hash=sha256_hash,
        approver_user_id=care_profile.user_id,
        consent_policy_id=consent_policy.id,
        ttl_seconds=86400,
    )
    db.add(approval)

    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.APPROVAL_GRANTED,
            actor="Trust Engine Policy Evaluator",
            role="SYSTEM",
            module="CARE",
            summary=f"Autonomous approval granted for Proposal #{proposal.id} under Mode B policy bounds.",
            entity_id=proposal.id,
            fingerprint=sha256_hash,
            details={"policy_id": consent_policy.id, "approval_type": "POLICY_AUTOMATIC"},
            severity=SeverityLevel.INFO,
        )
    )
    await db.flush()

    # Automatically create PayPal Sandbox order
    paypal_svc = PayPalService()
    order_resp = await paypal_svc.create_order_for_proposal(db, proposal.id)
    await db.commit()
    p_full = await load_proposal_full(db, proposal.id)

    tx_res = await db.execute(select(Transaction).where(Transaction.proposal_id == proposal.id))
    tx = tx_res.scalars().first()

    return CareReorderResponse(
        proposal=to_proposal_response(p_full, tx=tx, ev=eval_record, fingerprint_hash=sha256_hash),
        requires_approval=False,
        decision=PolicyDecision.ALLOW,
        human_explanation=eval_result.human_explanation,
        order=order_resp,
    )
