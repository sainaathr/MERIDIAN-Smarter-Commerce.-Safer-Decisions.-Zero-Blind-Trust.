from datetime import datetime, timezone, timedelta
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.enums import (
    TransactionLifecycleState,
    ModuleScope,
    PolicyDecision,
    ApprovalMode,
    ApprovalType,
    AuditEventType,
    SeverityLevel,
)
from app.models.entities import (
    User,
    ConsentPolicy,
    PurchaseProposal,
    ProposalItem,
    TransactionFingerprint,
    Approval,
    PolicyEvaluationRecord,
    AuditEvent,
    GuardianRelationship,
    CareProfile,
    ReorderSchedule,
    DelegatedPermission,
)
from app.schemas.domain import (
    ProposalCreate,
    ProposalResponse,
    ProposalItemResponse,
    FingerprintRequest,
    FingerprintResponse,
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    ApprovalCreate,
    ApprovalResponse,
    StateTransitionRequest,
    StateTransitionResponse,
    ConsentPolicyCreate,
    ConsentPolicyResponse,
    AuditEventResponse,
)
from app.engine.fingerprint import compute_transaction_fingerprint
from app.engine.lifecycle import (
    validate_transition,
    IllegalStateTransitionError,
    PAYMENT_RESTRICTED_STATES,
)
from app.engine.policy import evaluate_transaction_policy, PolicyEvaluationResult
from app.engine.approval import verify_approval_integrity, verify_approver_authority, ensure_utc
from app.engine.budget import release_care_budget, process_expired_reservations_and_proposals

router = APIRouter(prefix="/trust", tags=["Universal Trust Engine"])

def to_str(val: Any) -> str:
    """Helper to safely extract string representation from either Enum or str."""
    if val is None:
        return ""
    return val.value if hasattr(val, "value") else str(val)

# ==============================================================================
# 1. Cryptographic Fingerprinting Endpoint
# ==============================================================================
@router.post("/fingerprint", response_model=FingerprintResponse)
async def generate_fingerprint(req: FingerprintRequest):
    """
    Computes deterministic canonical JSON and SHA-256 fingerprint for material transaction parameters.
    """
    items_dict = [it.model_dump() for it in req.items]
    canonical_payload, sha256_hash = compute_transaction_fingerprint(
        user_id=req.user_id,
        module=to_str(req.module),
        merchant_id=req.merchant_id,
        merchant_name=req.merchant_name,
        items=items_dict,
        total_cents=req.total_cents,
        currency=req.currency,
        policy_version=req.policy_version,
        care_profile_id=req.care_profile_id,
    )
    return FingerprintResponse(
        canonical_payload=canonical_payload,
        sha256_hash=sha256_hash,
        policy_version=req.policy_version,
    )

# ==============================================================================
# 2. Policy Evaluation Endpoint
# ==============================================================================
@router.post("/evaluate", response_model=PolicyEvaluationResponse)
async def evaluate_policy_endpoint(req: PolicyEvaluationRequest, db: AsyncSession = Depends(get_db)):
    """
    Evaluates proposed transaction parameters against active deterministic policies.
    """
    await process_expired_reservations_and_proposals(db)

    if req.proposal_id:
        result = await db.execute(
            select(PurchaseProposal)
            .where(PurchaseProposal.id == req.proposal_id)
            .options(selectinload(PurchaseProposal.items), selectinload(PurchaseProposal.fingerprint))
        )
        proposal = result.scalar_one_or_none()
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found.")

        user_id = proposal.user_id
        care_profile_id = proposal.care_profile_id
        module = proposal.module
        merchant_id = proposal.merchant_id
        merchant_name = proposal.merchant_name
        items = [
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
        subtotal_cents = proposal.subtotal_cents
        tax_cents = proposal.tax_cents
        shipping_cents = proposal.shipping_cents
        total_cents = proposal.total_cents
        currency = proposal.currency
        recorded_hash = proposal.fingerprint.sha256_hash if proposal.fingerprint else ""
    elif req.proposal:
        p = req.proposal
        user_id = p.user_id
        care_profile_id = p.care_profile_id
        module = p.module
        merchant_id = p.merchant_id
        merchant_name = p.merchant_name
        items = [it.model_dump() for it in p.items]
        subtotal_cents = p.subtotal_cents
        tax_cents = p.tax_cents
        shipping_cents = p.shipping_cents
        total_cents = p.subtotal_cents + p.tax_cents + p.shipping_cents
        currency = p.currency
        _, recorded_hash = compute_transaction_fingerprint(
            user_id=user_id,
            module=to_str(module),
            merchant_id=merchant_id,
            merchant_name=merchant_name,
            items=items,
            total_cents=total_cents,
            currency=currency,
            care_profile_id=care_profile_id,
            subtotal_cents=subtotal_cents,
            tax_cents=tax_cents,
            shipping_cents=shipping_cents,
        )
    else:
        raise HTTPException(status_code=400, detail="Must provide either proposal_id or proposal payload.")

    # Recompute hash for verification
    pol_ver = proposal.fingerprint.policy_version if (proposal and proposal.fingerprint) else 1
    _, computed_hash = compute_transaction_fingerprint(
        user_id=user_id,
        module=to_str(module),
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        items=items,
        total_cents=total_cents,
        currency=currency,
        care_profile_id=care_profile_id,
        subtotal_cents=subtotal_cents,
        tax_cents=tax_cents,
        shipping_cents=shipping_cents,
        policy_version=pol_ver,
    )

    # Fetch active policy
    pol_result = await db.execute(
        select(ConsentPolicy)
        .where(ConsentPolicy.user_id == user_id, ConsentPolicy.scope == to_str(module))
        .order_by(desc(ConsentPolicy.policy_version))
    )
    policy = pol_result.scalars().first()
    policy_dict = (
        {
            "max_per_order_cents": policy.max_per_order_cents,
            "monthly_budget_cents": policy.monthly_budget_cents,
            "monthly_spent_cents": policy.monthly_spent_cents,
            "currency": policy.currency,
            "allowed_merchants": policy.allowed_merchants,
            "allowed_categories": policy.allowed_categories,
            "approval_mode": policy.approval_mode,
            "valid_until": policy.valid_until,
            "is_revoked": policy.is_revoked,
            "policy_version": policy.policy_version,
        }
        if policy
        else None
    )

    # Check for existing approval
    appr_result = await db.execute(
        select(Approval).where(Approval.fingerprint_hash == computed_hash)
    )
    approval = appr_result.scalars().first()
    approval_dict = (
        {
            "fingerprint_hash": approval.fingerprint_hash,
            "expires_at": approval.expires_at,
            "is_revoked": approval.is_revoked,
            "revoked_reason": approval.revoked_reason,
        }
        if approval
        else None
    )

    # Guardian delegation check if acting user != proposal user
    guardian_dict = None
    if req.acting_user_id != user_id:
        g_result = await db.execute(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_user_id == req.acting_user_id
            )
        )
        guardian = g_result.scalars().first()
        if guardian:
            guardian_dict = {
                "can_approve": guardian.can_approve,
                "max_approval_amount_cents": guardian.max_approval_amount_cents,
                "is_active": guardian.is_active,
            }

    # CARE Context Enrichment (Schedule, Baseline Prices, Medical Eligibility)
    reorder_sched_dict = None
    baseline_prices: dict[str, int] = {}
    medical_safety_violation = None

    if to_str(module) == "CARE":
        care_prof = None
        if care_profile_id:
            care_prof = await db.get(CareProfile, care_profile_id)
        if not care_prof and user_id:
            care_prof = await db.scalar(
                select(CareProfile).where(CareProfile.user_id == user_id)
            )

        if care_prof:
            if policy_dict:
                policy_dict["monthly_budget_cents"] = care_prof.monthly_budget_cents
                policy_dict["monthly_spent_cents"] = care_prof.monthly_spent_cents
                policy_dict["monthly_reserved_cents"] = care_prof.reserved_cents
                policy_dict["approval_mode"] = to_str(care_prof.approval_mode)
                policy_dict["requires_reorder_schedule"] = to_str(care_prof.approval_mode) == "CONTROLLED_AUTO"
                policy_dict["price_tolerance_pct"] = 10

            sched_res = await db.execute(
                select(ReorderSchedule).where(ReorderSchedule.care_profile_id == care_prof.id)
            )
            schedules = sched_res.scalars().all()

            for item in items:
                p_id = item.get("product_id", "")
                p_name = str(item.get("name", "")).lower()
                matched_s = None
                for s in schedules:
                    if s.id in p_id or s.product_name.lower() == p_name or (item.get("category") and s.category.lower() == str(item.get("category")).lower()):
                        matched_s = s
                        break
                if matched_s:
                    if not matched_s.auto_reorder_eligible:
                        medical_safety_violation = f"MEDICAL SAFETY RESTRICTION: Item '{item.get('name')}' is not eligible for automated reordering. Prescriptions and controlled substances require certified clinical authorization."
                    baseline_prices[p_id] = matched_s.unit_cost_cents
                    if reorder_sched_dict is None:
                        reorder_sched_dict = {
                            "schedule_id": matched_s.id,
                            "frequency_days": matched_s.frequency_days,
                            "is_active": True,
                            "tolerance_percent": 10,
                        }

    # Check if proposal is already in terminal REJECTED state
    if req.proposal_id and proposal:
        current_state_enum = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )
        if current_state_enum == TransactionLifecycleState.REJECTED:
            now_utc = datetime.now(timezone.utc)
            eval_record = PolicyEvaluationRecord(
                proposal_id=proposal.id,
                decision=PolicyDecision.DENY,
                rules_evaluated=["TERMINAL_REJECTED_STATE"],
                passed_checks=[],
                failed_checks=["PROPOSAL_ALREADY_REJECTED"],
                human_explanation="Proposal was already rejected by the Universal Trust Engine and is in a terminal state.",
                fingerprint_hash=recorded_hash,
                policy_version=policy.policy_version if policy else 1,
                created_at=now_utc,
            )
            db.add(eval_record)
            audit = AuditEvent(
                timestamp=now_utc,
                event_type=AuditEventType.POLICY_EVALUATED,
                actor=req.acting_user_id,
                role="ENGINE",
                module=to_str(module),
                summary="Evaluated policy verdict: DENY. Proposal was already rejected and is in a terminal state.",
                entity_id=req.proposal_id,
                decision="DENY",
                fingerprint=recorded_hash,
                details={
                    "rules_evaluated": ["TERMINAL_REJECTED_STATE"],
                    "passed": [],
                    "failed": ["PROPOSAL_ALREADY_REJECTED"],
                },
                severity=SeverityLevel.ALERT,
            )
            db.add(audit)
            await db.commit()
            return PolicyEvaluationResponse(
                decision=PolicyDecision.DENY,
                rules_evaluated=["TERMINAL_REJECTED_STATE"],
                passed_checks=[],
                failed_checks=["PROPOSAL_ALREADY_REJECTED"],
                human_explanation="Proposal was already rejected by the Universal Trust Engine and is in a terminal state.",
                fingerprint_hash=recorded_hash,
                policy_version=policy.policy_version if policy else 1,
                timestamp=now_utc,
                requires_approval=False,
            )

    if medical_safety_violation:
        eval_result = PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=["RULE_MEDICAL_SAFETY_RESTRICTIONS"],
            passed_checks=[],
            failed_checks=["INELIGIBLE_FOR_AUTO_REORDER"],
            human_explanation=medical_safety_violation,
            fingerprint_hash=recorded_hash,
            policy_version=policy.policy_version if policy else 1,
            requires_approval=False,
        )
    else:
        eval_result = evaluate_transaction_policy(
            user_id=user_id,
            module=to_str(module),
            merchant_id=merchant_id,
            merchant_name=merchant_name,
            items=items,
            total_cents=total_cents,
            currency=currency,
            fingerprint_hash=recorded_hash,
            computed_fingerprint_hash=computed_hash,
            policy=policy_dict,
            acting_user_id=req.acting_user_id,
            guardian_delegation=guardian_dict,
            existing_approval=approval_dict,
            reorder_schedule=reorder_sched_dict,
            baseline_item_prices=baseline_prices,
        )

    # Persist policy evaluation record for proposals and handle state rejection
    if req.proposal_id and proposal:
        eval_record = PolicyEvaluationRecord(
            proposal_id=proposal.id,
            decision=eval_result.decision,
            rules_evaluated=eval_result.rules_evaluated,
            passed_checks=eval_result.passed_checks,
            failed_checks=eval_result.failed_checks,
            human_explanation=eval_result.human_explanation,
            fingerprint_hash=eval_result.fingerprint_hash,
            policy_version=eval_result.policy_version or 1,
            created_at=eval_result.timestamp,
        )
        db.add(eval_record)

        if eval_result.decision == PolicyDecision.DENY:
            previous_state_str = to_str(proposal.state)
            proposal.state = TransactionLifecycleState.REJECTED
            proposal.version += 1
            proposal.updated_at = eval_result.timestamp

            reject_audit = AuditEvent(
                timestamp=eval_result.timestamp,
                event_type=AuditEventType.STATE_TRANSITION,
                actor=req.acting_user_id,
                role="ENGINE",
                module=to_str(module),
                summary=f"Proposal #{proposal.id} automatically transitioned to REJECTED due to Trust Engine policy denial: {eval_result.human_explanation}",
                entity_id=proposal.id,
                previous_state=previous_state_str,
                new_state=to_str(TransactionLifecycleState.REJECTED),
                decision=to_str(eval_result.decision),
                fingerprint=eval_result.fingerprint_hash,
                details={
                    "reason": eval_result.human_explanation,
                    "failed_checks": eval_result.failed_checks,
                },
                severity=SeverityLevel.ALERT,
            )
            db.add(reject_audit)

    # Record evaluation in audit log
    audit = AuditEvent(
        timestamp=eval_result.timestamp,
        event_type=AuditEventType.POLICY_EVALUATED,
        actor=req.acting_user_id,
        role="ENGINE",
        module=to_str(module),
        summary=f"Evaluated policy verdict: {to_str(eval_result.decision)}. {eval_result.human_explanation}",
        entity_id=req.proposal_id,
        decision=to_str(eval_result.decision),
        fingerprint=eval_result.fingerprint_hash,
        details={
            "rules_evaluated": eval_result.rules_evaluated,
            "passed": eval_result.passed_checks,
            "failed": eval_result.failed_checks,
        },
        severity=SeverityLevel.ALERT if eval_result.decision == PolicyDecision.DENY else SeverityLevel.INFO,
    )
    db.add(audit)
    await db.commit()

    return PolicyEvaluationResponse(
        decision=eval_result.decision,
        rules_evaluated=eval_result.rules_evaluated,
        passed_checks=eval_result.passed_checks,
        failed_checks=eval_result.failed_checks,
        human_explanation=eval_result.human_explanation,
        fingerprint_hash=eval_result.fingerprint_hash,
        policy_version=eval_result.policy_version,
        timestamp=eval_result.timestamp,
        requires_approval=eval_result.requires_approval,
    )

# ==============================================================================
# 3. Proposal Creation & Retrieval
# ==============================================================================
@router.post("/proposals", response_model=ProposalResponse)
async def create_proposal(req: ProposalCreate, db: AsyncSession = Depends(get_db)):
    """
    Creates a new purchase proposal, automatically generating and binding its cryptographic fingerprint.
    Validates arithmetic consistency across line items, subtotal, tax, and shipping.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="Proposal must contain at least one line item.")

    calculated_subtotal = sum(it.quantity * it.unit_price_cents for it in req.items)
    if calculated_subtotal != req.subtotal_cents:
        raise HTTPException(
            status_code=400,
            detail=f"Line items total ({calculated_subtotal} cents) does not match subtotal_cents ({req.subtotal_cents} cents).",
        )

    total_cents = req.subtotal_cents + req.tax_cents + req.shipping_cents

    proposal = PurchaseProposal(
        user_id=req.user_id,
        care_profile_id=req.care_profile_id,
        module=to_str(req.module),
        title=req.title,
        merchant_id=req.merchant_id,
        merchant_name=req.merchant_name,
        subtotal_cents=req.subtotal_cents,
        tax_cents=req.tax_cents,
        shipping_cents=req.shipping_cents,
        total_cents=total_cents,
        currency=req.currency,
        ai_reasoning=req.ai_reasoning,
        state=TransactionLifecycleState.PROPOSED,
    )
    db.add(proposal)
    await db.flush()

    # Add Line Items
    items_for_fingerprint = []
    proposal_items = []
    for it in req.items:
        line_total = it.quantity * it.unit_price_cents
        pi = ProposalItem(
            proposal_id=proposal.id,
            product_id=it.product_id,
            name=it.name,
            sku=it.sku,
            category=it.category,
            quantity=it.quantity,
            unit_price_cents=it.unit_price_cents,
            total_cents=line_total,
        )
        proposal_items.append(pi)
        items_for_fingerprint.append({
            "product_id": it.product_id,
            "name": it.name,
            "sku": it.sku,
            "category": it.category,
            "quantity": it.quantity,
            "unit_price_cents": it.unit_price_cents,
            "total_cents": line_total,
        })
    db.add_all(proposal_items)

    # Compute Fingerprint
    canonical_payload, sha256_hash = compute_transaction_fingerprint(
        user_id=req.user_id,
        module=to_str(req.module),
        merchant_id=req.merchant_id,
        merchant_name=req.merchant_name,
        items=items_for_fingerprint,
        total_cents=total_cents,
        currency=req.currency,
        care_profile_id=req.care_profile_id,
        subtotal_cents=req.subtotal_cents,
        tax_cents=req.tax_cents,
        shipping_cents=req.shipping_cents,
    )

    fingerprint = TransactionFingerprint(
        proposal_id=proposal.id,
        canonical_payload=canonical_payload,
        sha256_hash=sha256_hash,
        policy_version=1,
    )
    db.add(fingerprint)

    # Audit event
    audit = AuditEvent(
        timestamp=datetime.now(timezone.utc),
        event_type=AuditEventType.PROPOSAL_CREATED,
        actor=req.user_id,
        role="PROPOSER",
        module=to_str(req.module),
        summary=f"Created Proposal '{req.title}' bound to SHA-256 fingerprint {sha256_hash[:16]}...",
        entity_id=proposal.id,
        fingerprint=sha256_hash,
        details={"total_cents": total_cents, "items_count": len(req.items)},
        severity=SeverityLevel.INFO,
    )
    db.add(audit)

    await db.commit()
    await db.refresh(proposal)

    return ProposalResponse(
        id=proposal.id,
        user_id=proposal.user_id,
        care_profile_id=proposal.care_profile_id,
        module=proposal.module,
        title=proposal.title,
        merchant_id=proposal.merchant_id,
        merchant_name=proposal.merchant_name,
        items=[
            ProposalItemResponse(
                id=pi.id,
                product_id=pi.product_id,
                name=pi.name,
                sku=pi.sku,
                category=pi.category,
                quantity=pi.quantity,
                unit_price_cents=pi.unit_price_cents,
                total_cents=pi.total_cents,
            )
            for pi in proposal_items
        ],
        subtotal_cents=proposal.subtotal_cents,
        tax_cents=proposal.tax_cents,
        shipping_cents=proposal.shipping_cents,
        total_cents=proposal.total_cents,
        currency=proposal.currency,
        ai_reasoning=proposal.ai_reasoning,
        state=proposal.state,
        version=proposal.version,
        created_at=proposal.created_at,
        fingerprint_sha256=sha256_hash,
    )

def _build_proposal_response(p: PurchaseProposal) -> ProposalResponse:
    tx = p.transactions[-1] if p.transactions else None
    ev = p.evaluations[-1] if p.evaluations else None
    state_str = to_str(p.state)
    decision_str = to_str(ev.decision) if ev else None
    is_awaiting = (state_str == "AWAITING_APPROVAL")

    if state_str == "APPROVED":
        approval_status = "APPROVED"
    elif is_awaiting:
        if p.care_profile_id or to_str(p.module) == "CARE":
            approval_status = "AWAITING_GUARDIAN_CONSENT"
        else:
            approval_status = "AWAITING_HUMAN_APPROVAL"
    elif state_str in {"REJECTED", "CANCELLED", "EXPIRED", "FAILED"}:
        approval_status = state_str
    elif state_str in {"PAYMENT_PENDING", "PAID"}:
        approval_status = state_str
    else:
        approval_status = state_str

    requires_approval = is_awaiting or (decision_str == "REQUIRE_APPROVAL" and state_str not in {"APPROVED", "PAID", "REJECTED"})

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
        fingerprint_sha256=p.fingerprint.sha256_hash if p.fingerprint else None,
        policy_decision=decision_str,
        approval_status=approval_status,
        requires_approval=requires_approval,
        paypal_order_id=tx.paypal_order_id if tx else None,
        paypal_capture_id=tx.paypal_capture_id if tx else None,
        paypal_status=tx.paypal_status if tx else None,
        is_simulated=tx.is_simulated if tx else None,
        updated_at=tx.updated_at if tx else p.updated_at,
    )

@router.get("/proposals", response_model=List[ProposalResponse])
async def list_proposals(
    module: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves authoritative persisted proposals ordered newest first,
    including linked transactions, payment status, and policy evaluation verdicts.
    """
    await process_expired_reservations_and_proposals(db)

    query = (
        select(PurchaseProposal)
        .options(
            selectinload(PurchaseProposal.items),
            selectinload(PurchaseProposal.fingerprint),
            selectinload(PurchaseProposal.transactions),
            selectinload(PurchaseProposal.evaluations),
        )
        .order_by(desc(PurchaseProposal.created_at))
        .offset(offset)
        .limit(limit)
    )
    if module and module.upper() != "ALL":
        query = query.where(PurchaseProposal.module == module.upper())
    if status and status.upper() != "ALL":
        query = query.where(PurchaseProposal.state == status.upper())

    result = await db.execute(query)
    proposals = result.scalars().all()

    return [_build_proposal_response(p) for p in proposals]

@router.get("/proposals/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(proposal_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PurchaseProposal)
        .where(PurchaseProposal.id == proposal_id)
        .options(
            selectinload(PurchaseProposal.items),
            selectinload(PurchaseProposal.fingerprint),
            selectinload(PurchaseProposal.transactions),
            selectinload(PurchaseProposal.evaluations),
        )
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found.")

    return _build_proposal_response(p)

# ==============================================================================
# 4. Human Approval Endpoint
# ==============================================================================
@router.post("/approve", response_model=ApprovalResponse)
async def grant_approval(req: ApprovalCreate, db: AsyncSession = Depends(get_db)):
    """
    Records explicit human approval bound cryptographically to the exact transaction fingerprint.
    """
    # Verify proposal exists
    result = await db.execute(
        select(PurchaseProposal)
        .where(PurchaseProposal.id == req.proposal_id)
        .options(selectinload(PurchaseProposal.fingerprint), selectinload(PurchaseProposal.items))
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found.")

    if not proposal.fingerprint:
        raise HTTPException(status_code=400, detail="Proposal has no registered cryptographic fingerprint.")

    # Invariant: Fingerprint must match exact proposal hash and authoritative persisted state
    items_for_fingerprint = [
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
    _, authoritative_hash = compute_transaction_fingerprint(
        user_id=proposal.user_id,
        module=to_str(proposal.module),
        merchant_id=proposal.merchant_id,
        merchant_name=proposal.merchant_name,
        items=items_for_fingerprint,
        total_cents=proposal.total_cents,
        currency=proposal.currency,
        care_profile_id=proposal.care_profile_id,
        subtotal_cents=proposal.subtotal_cents,
        tax_cents=proposal.tax_cents,
        shipping_cents=proposal.shipping_cents,
        policy_version=proposal.fingerprint.policy_version if proposal.fingerprint else 1,
    )

    if req.fingerprint_hash != authoritative_hash or proposal.fingerprint.sha256_hash != authoritative_hash:
        raise HTTPException(
            status_code=400,
            detail="Fingerprint mismatch: Client-supplied or stored hash does not match authoritative persisted transaction state.",
        )

    # Defect 1: Disallow external manufacturing of POLICY_AUTOMATIC approvals through human endpoint
    if req.approval_type == ApprovalType.POLICY_AUTOMATIC or to_str(req.approval_type) == "POLICY_AUTOMATIC":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Automatic policy approval cannot be granted through the public human approval endpoint. It must be generated by the server-side Trust Engine policy evaluation.",
        )

    current_state_enum = (
        proposal.state
        if isinstance(proposal.state, TransactionLifecycleState)
        else TransactionLifecycleState(str(proposal.state))
    )
    if current_state_enum in {
        TransactionLifecycleState.REJECTED,
        TransactionLifecycleState.CANCELLED,
        TransactionLifecycleState.EXPIRED,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: Cannot approve a proposal in {to_str(current_state_enum)} state.",
        )
    if current_state_enum == TransactionLifecycleState.APPROVED:
        # Idempotent return: check if an active, unrevoked approval already exists for this proposal & approver
        existing_app = await db.scalar(
            select(Approval)
            .where(
                Approval.fingerprint_hash == authoritative_hash,
                Approval.approver_user_id == req.approver_user_id,
                Approval.is_revoked == False,
            )
            .order_by(desc(Approval.created_at))
        )
        now_check = datetime.now(timezone.utc)
        if existing_app and ensure_utc(existing_app.expires_at) > now_check:
            return existing_app
        raise HTTPException(
            status_code=400,
            detail=f"Proposal #{proposal.id} is already in APPROVED state, but its prior approval was revoked or expired. Re-evaluation through Universal Trust Engine is required.",
        )
    if current_state_enum != TransactionLifecycleState.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Proposal #{proposal.id} is in state {to_str(current_state_enum)}, expected AWAITING_APPROVAL.",
        )

    # Invariant: Disallow human approval if Trust Engine policy decision is DENY
    eval_query = (
        select(PolicyEvaluationRecord)
        .where(PolicyEvaluationRecord.proposal_id == proposal.id)
        .order_by(desc(PolicyEvaluationRecord.created_at))
    )
    eval_res = await db.execute(eval_query)
    latest_eval = eval_res.scalars().first()
    if latest_eval and (latest_eval.decision == PolicyDecision.DENY or to_str(latest_eval.decision) == "DENY"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: Cannot approve proposal. Universal Trust Engine policy decision is DENY: {latest_eval.human_explanation}",
        )

    # Approver Authority & Persisted Relationship Verification (Finding 1)
    # (Controlled synthetic-demo authorization boundary: enforces persisted user allowlisting and guardian delegations)
    approver = await db.scalar(
        select(User).where(User.id == req.approver_user_id, User.is_active == True)
    )
    if not approver:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: Approver user '{req.approver_user_id}' does not exist or is inactive.",
        )

    proposal_module_str = to_str(proposal.module)
    item_category = proposal.items[0].category if proposal.items else None

    if proposal_module_str == "CARE" or proposal.care_profile_id:
        care_prof = None
        if proposal.care_profile_id:
            care_prof = await db.get(CareProfile, proposal.care_profile_id)
        if not care_prof:
            care_prof = await db.scalar(
                select(CareProfile).where(CareProfile.user_id == proposal.user_id)
            )

        guardian_dict = None
        if care_prof and req.approver_user_id != proposal.user_id:
            g_rel = await db.scalar(
                select(GuardianRelationship)
                .where(
                    GuardianRelationship.care_profile_id == care_prof.id,
                    GuardianRelationship.guardian_user_id == req.approver_user_id,
                )
                .options(selectinload(GuardianRelationship.permissions))
            )
            if g_rel:
                guardian_dict = {
                    "relationship_type": g_rel.relationship_type,
                    "can_approve": g_rel.can_approve,
                    "max_approval_amount_cents": g_rel.max_approval_amount_cents,
                    "is_active": g_rel.is_active,
                    "permissions": [
                        {
                            "category": p.category,
                            "max_per_order_cents": p.max_per_order_cents,
                            "is_active": p.is_active,
                        }
                        for p in g_rel.permissions
                    ],
                }

        auth_check = verify_approver_authority(
            approver_user_id=req.approver_user_id,
            proposal_user_id=proposal.user_id,
            guardian_relationship=guardian_dict,
            total_cents=proposal.total_cents,
            category=item_category,
        )
        if not auth_check.is_valid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: {auth_check.reason}",
            )
    else:
        if req.approver_user_id != proposal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User '{req.approver_user_id}' is not authorized to approve proposals for user '{proposal.user_id}'.",
            )

    # Revalidate Consent Policy
    now_utc = datetime.now(timezone.utc)
    consent_policy = None
    if req.consent_policy_id:
        consent_policy = await db.get(ConsentPolicy, req.consent_policy_id)
    if not consent_policy:
        consent_policy = await db.scalar(
            select(ConsentPolicy).where(
                ConsentPolicy.user_id == proposal.user_id,
                ConsentPolicy.scope == proposal.module,
            )
        )
    if consent_policy:
        if consent_policy.is_revoked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Underlying consent policy was revoked, invalidating approval.",
            )
        valid_until_utc = ensure_utc(consent_policy.valid_until)
        if valid_until_utc and valid_until_utc < now_utc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Underlying consent policy has expired, invalidating approval.",
            )

    expires_at = now_utc + timedelta(seconds=req.ttl_seconds)

    approval = Approval(
        fingerprint_hash=req.fingerprint_hash,
        approver_user_id=req.approver_user_id,
        approval_type=req.approval_type,
        consent_policy_id=consent_policy.id if consent_policy else req.consent_policy_id,
        expires_at=expires_at,
        is_revoked=False,
    )
    db.add(approval)

    # Transition state to APPROVED
    try:
        current_state_enum = (
            proposal.state
            if isinstance(proposal.state, TransactionLifecycleState)
            else TransactionLifecycleState(str(proposal.state))
        )
        validate_transition(current_state_enum, TransactionLifecycleState.APPROVED)
        proposal.state = TransactionLifecycleState.APPROVED
    except IllegalStateTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Audit event
    audit = AuditEvent(
        timestamp=now_utc,
        event_type=AuditEventType.APPROVAL_GRANTED,
        actor=req.approver_user_id,
        role="APPROVER",
        module=to_str(proposal.module),
        summary=f"Explicit approval granted for Proposal #{proposal.id} bound to fingerprint {req.fingerprint_hash[:16]}...",
        entity_id=proposal.id,
        fingerprint=req.fingerprint_hash,
        previous_state=to_str(TransactionLifecycleState.AWAITING_APPROVAL),
        new_state=to_str(TransactionLifecycleState.APPROVED),
        details={"expires_at": expires_at.isoformat(), "approval_type": to_str(req.approval_type)},
        severity=SeverityLevel.SUCCESS,
    )
    db.add(audit)

    await db.commit()
    await db.refresh(approval)
    return approval

# ==============================================================================
# 5. Lifecycle Transition Endpoint
# ==============================================================================
@router.post("/transactions/{proposal_id}/transition", response_model=StateTransitionResponse)
async def transition_state(
    proposal_id: str,
    req: StateTransitionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Executes an explicit legal transaction lifecycle state transition.
    """
    result = await db.execute(
        select(PurchaseProposal).where(PurchaseProposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found.")

    current_state_enum = (
        proposal.state
        if isinstance(proposal.state, TransactionLifecycleState)
        else TransactionLifecycleState(str(proposal.state))
    )
    target_state_enum = (
        req.target_state
        if isinstance(req.target_state, TransactionLifecycleState)
        else TransactionLifecycleState(str(req.target_state))
    )

    # Invariant: Rejected proposals are in a terminal state and cannot be transitioned
    if current_state_enum == TransactionLifecycleState.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Proposal has been rejected by policy and cannot be transitioned.",
        )

    # Invariant: Disallow advancing proposals with a Trust Engine DENY policy verdict
    if target_state_enum in {
        TransactionLifecycleState.AWAITING_APPROVAL,
        TransactionLifecycleState.APPROVED,
        TransactionLifecycleState.PAYMENT_PENDING,
        TransactionLifecycleState.PAID,
    }:
        eval_query = (
            select(PolicyEvaluationRecord)
            .where(PolicyEvaluationRecord.proposal_id == proposal_id)
            .order_by(desc(PolicyEvaluationRecord.created_at))
        )
        eval_res = await db.execute(eval_query)
        latest_eval = eval_res.scalars().first()
        if latest_eval and (latest_eval.decision == PolicyDecision.DENY or to_str(latest_eval.decision) == "DENY"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Cannot advance proposal with a Trust Engine 'DENY' policy decision: {latest_eval.human_explanation}",
            )

    # CRITICAL INVARIANT: Generic lifecycle APIs must NEVER authorize transitions to payment-restricted states (PAID).
    # Genuine PAID can ONLY be reached through the trusted payment service following verified provider capture.
    if target_state_enum in PAYMENT_RESTRICTED_STATES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Forbidden state transition: Transition to restricted state '{target_state_enum.value}' "
                "cannot be requested through generic lifecycle APIs. Only verified payment execution "
                "subsystems may authorize state 'PAID'."
            ),
        )

    try:
        validate_transition(current_state_enum, target_state_enum)
    except IllegalStateTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    current_version = proposal.version
    if req.expected_version is not None and current_version != req.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Optimistic concurrency conflict: proposal version is {current_version}, expected {req.expected_version}.",
        )

    # Atomic state transition with version increment
    stmt = (
        update(PurchaseProposal)
        .where(
            PurchaseProposal.id == proposal_id,
            PurchaseProposal.version == current_version,
            PurchaseProposal.state == to_str(current_state_enum),
        )
        .values(
            state=to_str(target_state_enum),
            version=PurchaseProposal.version + 1,
        )
    )
    update_res = await db.execute(stmt)
    if update_res.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail="Optimistic concurrency conflict: transaction state was modified by a concurrent operation.",
        )

    # Defect 2: Central, idempotent reservation release on terminal non-payment transition
    if target_state_enum in {
        TransactionLifecycleState.REJECTED,
        TransactionLifecycleState.CANCELLED,
        TransactionLifecycleState.EXPIRED,
    }:
        await release_care_budget(db, proposal_id, is_simulated=False)

    now_utc = datetime.now(timezone.utc)

    # Record transition audit event
    audit = AuditEvent(
        timestamp=now_utc,
        event_type=AuditEventType.STATE_TRANSITION,
        actor=req.actor_id,
        role=req.actor_role,
        module=to_str(proposal.module),
        summary=f"Transitioned Proposal #{proposal.id} from {to_str(current_state_enum)} to {to_str(target_state_enum)}",
        entity_id=proposal.id,
        previous_state=to_str(current_state_enum),
        new_state=to_str(target_state_enum),
        details={"reason": req.reason},
        severity=SeverityLevel.INFO,
    )
    db.add(audit)

    await db.commit()

    return StateTransitionResponse(
        success=True,
        proposal_id=proposal.id,
        previous_state=current_state_enum,
        new_state=target_state_enum,
        timestamp=now_utc,
    )

# ==============================================================================
# 6. Consent Policies Endpoints
# ==============================================================================
@router.get("/policies", response_model=List[ConsentPolicyResponse])
async def list_policies(user_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    query = select(ConsentPolicy).order_by(desc(ConsentPolicy.created_at))
    if user_id:
        query = query.where(ConsentPolicy.user_id == user_id)
    result = await db.execute(query)
    return result.scalars().all()

@router.post("/policies", response_model=ConsentPolicyResponse)
async def create_policy(req: ConsentPolicyCreate, db: AsyncSession = Depends(get_db)):
    policy = ConsentPolicy(
        user_id=req.user_id,
        scope=to_str(req.scope),
        title=req.title,
        max_per_order_cents=req.max_per_order_cents,
        monthly_budget_cents=req.monthly_budget_cents,
        monthly_spent_cents=0,
        currency=req.currency,
        allowed_merchants=req.allowed_merchants,
        allowed_categories=req.allowed_categories,
        approval_mode=to_str(req.approval_mode),
        valid_until=req.valid_until,
        is_revoked=False,
        policy_version=1,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy

@router.post("/policies/{policy_id}/revoke")
async def revoke_policy(policy_id: str, reason: str = "User initiated revocation", db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ConsentPolicy).where(ConsentPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found.")

    policy.is_revoked = True

    # Audit event for policy revocation
    audit = AuditEvent(
        timestamp=datetime.now(timezone.utc),
        event_type=AuditEventType.APPROVAL_REVOKED,
        actor=policy.user_id,
        role="OWNER",
        module=to_str(policy.scope),
        summary=f"Revoked Consent Policy #{policy.id} ({policy.title}). Reason: {reason}",
        entity_id=policy.id,
        details={"reason": reason},
        severity=SeverityLevel.WARNING,
    )
    db.add(audit)

    await db.commit()
    return {"status": "revoked", "policy_id": policy.id, "reason": reason}

@router.post("/policies/{policy_id}/reserve-budget")
async def reserve_policy_budget(
    policy_id: str,
    amount_cents: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Atomically checks remaining monthly budget and commits spending reservation.
    Ensures two simultaneous transactions cannot independently exceed the remaining monthly budget.
    """
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Reservation amount must be positive.")

    # Atomic conditional update: only succeeds if (monthly_spent_cents + amount_cents) <= monthly_budget_cents
    stmt = (
        update(ConsentPolicy)
        .where(
            ConsentPolicy.id == policy_id,
            ConsentPolicy.is_revoked == False,
            (ConsentPolicy.monthly_spent_cents + amount_cents) <= ConsentPolicy.monthly_budget_cents,
        )
        .values(
            monthly_spent_cents=ConsentPolicy.monthly_spent_cents + amount_cents
        )
    )
    res = await db.execute(stmt)
    if res.rowcount == 0:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="BUDGET DENIAL: Insufficient remaining monthly budget or policy is revoked.",
        )

    await db.commit()
    return {
        "success": True,
        "policy_id": policy_id,
        "reserved_cents": amount_cents,
        "message": f"Successfully reserved {amount_cents} cents against policy {policy_id}.",
    }

# ==============================================================================
# 7. Audit Event Retrieval Endpoint
# ==============================================================================
@router.get("/audit", response_model=List[AuditEventResponse])
async def list_audit_events(
    limit: int = 100,
    offset: int = 0,
    severity: Optional[str] = None,
    module: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves immutable audit trail events ordered newest first.
    Supports safe pagination and severity/module filtering.
    """
    query = (
        select(AuditEvent)
        .order_by(desc(AuditEvent.timestamp))
        .offset(offset)
        .limit(limit)
    )
    if severity and severity.upper() != "ALL":
        query = query.where(AuditEvent.severity == severity.upper())
    if module and module.upper() != "ALL":
        query = query.where(AuditEvent.module == module.upper())

    result = await db.execute(query)
    return result.scalars().all()
