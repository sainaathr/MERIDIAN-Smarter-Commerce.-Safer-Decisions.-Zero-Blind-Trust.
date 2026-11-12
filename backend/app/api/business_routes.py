import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.entities import (
    BusinessProfile,
    Invoice,
    PurchaseProposal,
    ProposalItem,
    TransactionFingerprint,
    PolicyEvaluationRecord,
    AuditEvent,
    Transaction,
    ConsentPolicy,
    Approval,
)
from app.models.enums import (
    ModuleScope,
    TransactionLifecycleState,
    PolicyDecision,
    AuditEventType,
    SeverityLevel,
)
from app.engine.fingerprint import compute_transaction_fingerprint
from app.engine.policy import evaluate_transaction_policy
from app.engine.business import (
    calculate_cashflow_forecast,
    check_business_affordability,
    ensure_utc,
)
from app.schemas.domain import (
    BusinessProfileResponse,
    InvoiceResponse,
    InvoiceCreateRequest,
    CashflowForecastResponse,
    BusinessProposalCreateRequest,
    BusinessAffordabilityCheckResponse,
    ProposalResponse,
    ProposalItemResponse,
)

router = APIRouter()

def to_str(val: Any) -> str:
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


# ==============================================================================
# 1. Authoritative Business Profile & Financial Metrics
# ==============================================================================

@router.get("/profile", response_model=BusinessProfileResponse)
async def get_business_profile(
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Returns authoritative business profile and financial runway metrics from persistent storage.
    """
    bp_res = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    bp = bp_res.scalar_one_or_none()
    if not bp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business profile for user '{user_id}' not found.",
        )

    # Query invoices to compute unpaid metrics
    inv_res = await db.execute(
        select(Invoice).where(Invoice.business_profile_id == bp.id)
    )
    invoices = inv_res.scalars().all()

    forecast = calculate_cashflow_forecast(bp, invoices)

    return BusinessProfileResponse(
        id=bp.id,
        user_id=bp.user_id,
        company_name=bp.company_name,
        operating_cash_cents=bp.operating_cash_cents,
        operating_cash=round(bp.operating_cash_cents / 100.0, 2),
        minimum_safety_reserve_cents=bp.minimum_safety_reserve_cents,
        minimum_safety_reserve=round(bp.minimum_safety_reserve_cents / 100.0, 2),
        currency=bp.currency or "USD",
        estimated_runway_days=forecast.estimated_runway_days,
        total_unpaid_receivables_cents=forecast.total_receivables_cents,
        total_unpaid_payables_cents=forecast.total_payables_cents,
        projected_net_liquidity_cents=bp.operating_cash_cents + forecast.total_receivables_cents - forecast.total_payables_cents,
    )


# ==============================================================================
# 2. Invoices Management
# ==============================================================================

@router.get("/invoices", response_model=List[InvoiceResponse])
async def list_invoices(
    invoice_type: Optional[str] = Query(None, description="RECEIVABLE or PAYABLE"),
    status_filter: Optional[str] = Query(None, alias="status", description="PENDING, PAID, OVERDUE, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Returns persisted business invoices with optional filtering by type and status.
    """
    bp_res = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    bp = bp_res.scalar_one_or_none()
    if not bp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business profile for user '{user_id}' not found.",
        )

    query = select(Invoice).where(Invoice.business_profile_id == bp.id)
    if invoice_type:
        query = query.where(Invoice.invoice_type == invoice_type.strip().upper())
    if status_filter:
        query = query.where(Invoice.status == status_filter.strip().upper())

    query = query.order_by(Invoice.due_date.asc()).limit(limit).offset(offset)
    res = await db.execute(query)
    invoices = res.scalars().all()

    return [
        InvoiceResponse(
            id=inv.id,
            business_profile_id=inv.business_profile_id,
            counterparty=inv.counterparty,
            invoice_type=inv.invoice_type,
            amount_cents=inv.amount_cents,
            amount=round(inv.amount_cents / 100.0, 2),
            due_date=ensure_utc(inv.due_date),
            status=inv.status,
            category=inv.category,
        )
        for inv in invoices
    ]


@router.post("/invoices", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    req: InvoiceCreateRequest,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new validated business invoice obligation or receivable.
    """
    bp_res = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    bp = bp_res.scalar_one_or_none()
    if not bp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business profile for user '{user_id}' not found.",
        )

    now_utc = datetime.now(timezone.utc)
    due_utc = ensure_utc(req.due_date)

    inv_id = f"inv-{uuid.uuid4().hex[:8]}"
    invoice = Invoice(
        id=inv_id,
        business_profile_id=bp.id,
        counterparty=req.counterparty.strip(),
        invoice_type=req.invoice_type,
        amount_cents=req.amount_cents,
        due_date=due_utc,
        status="PENDING",
        category=req.category.strip(),
    )
    db.add(invoice)

    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.STATE_TRANSITION,
            actor=user_id,
            role="BUSINESS_ADMIN",
            module="BUSINESS",
            summary=f"Recorded new {req.invoice_type} invoice #{inv_id} for {req.counterparty} (${req.amount_cents / 100:,.2f})",
            entity_id=inv_id,
            new_state="PENDING",
            details={
                "business_profile_id": bp.id,
                "counterparty": req.counterparty,
                "invoice_type": req.invoice_type,
                "amount_cents": req.amount_cents,
                "category": req.category,
            },
            severity=SeverityLevel.INFO,
        )
    )

    await db.commit()
    await db.refresh(invoice)

    return InvoiceResponse(
        id=invoice.id,
        business_profile_id=invoice.business_profile_id,
        counterparty=invoice.counterparty,
        invoice_type=invoice.invoice_type,
        amount_cents=invoice.amount_cents,
        amount=round(invoice.amount_cents / 100.0, 2),
        due_date=ensure_utc(invoice.due_date),
        status=invoice.status,
        category=invoice.category,
    )


# ==============================================================================
# 3. Deterministic 30-Day Cashflow Projection
# ==============================================================================

@router.get("/cashflow", response_model=CashflowForecastResponse)
async def get_cashflow_forecast(
    horizon_days: int = Query(30, ge=7, le=90),
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Computes deterministic 30-day daily cumulative cashflow forecast.
    Strict integer cents arithmetic; zero LLM hallucination.
    """
    bp_res = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    bp = bp_res.scalar_one_or_none()
    if not bp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business profile for user '{user_id}' not found.",
        )

    inv_res = await db.execute(
        select(Invoice).where(Invoice.business_profile_id == bp.id)
    )
    invoices = inv_res.scalars().all()

    return calculate_cashflow_forecast(bp, invoices, horizon_days=horizon_days)


# ==============================================================================
# 4. Affordability Check Endpoint
# ==============================================================================

@router.post("/affordability-check", response_model=BusinessAffordabilityCheckResponse)
async def run_affordability_check(
    payload: Dict[str, Any],
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Evaluates Gate A financial affordability for a proposed purchase amount in cents.
    Confirms whether projected cash remains above the $8,000.00 minimum safety reserve.
    """
    amount_cents = payload.get("amount_cents")
    if amount_cents is None or not isinstance(amount_cents, int):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field 'amount_cents' must be an integer.",
        )

    bp_res = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    bp = bp_res.scalar_one_or_none()
    if not bp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business profile for user '{user_id}' not found.",
        )

    inv_res = await db.execute(
        select(Invoice).where(Invoice.business_profile_id == bp.id)
    )
    invoices = inv_res.scalars().all()

    return check_business_affordability(bp, invoices, proposed_amount_cents=amount_cents)


# ==============================================================================
# 5. Business Purchase Proposal Creation & Dual-Gate Enforcement
# ==============================================================================

@router.post("/proposals", response_model=ProposalResponse, status_code=status.HTTP_201_CREATED)
async def create_business_proposal(
    req: BusinessProposalCreateRequest,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Creates an affordability-verified and policy-evaluated supplier purchase proposal.

    Enforces Separate Authorization Gates:
    1. Gate A — Financial Affordability:
       The deterministic affordability engine verifies that the purchase preserves the
       statutory minimum safety reserve ($8,000.00). Unsafe purchases fail closed (HTTP 400).
    2. Gate B — Universal Trust Engine:
       Eligible proposals are evaluated against policy `pol-003` (merchant whitelist,
       categories, order ceiling $300.00, monthly budget $1,200.00).
       Always requires explicit human approval (Mode A).
    """
    now_utc = datetime.now(timezone.utc)

    # 1. Line item arithmetic validation
    calculated_subtotal = sum(item.quantity * item.unit_price_cents for item in req.items)
    if calculated_subtotal != req.subtotal_cents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sum of item totals ({calculated_subtotal} cents) does not match subtotal_cents ({req.subtotal_cents} cents).",
        )

    total_cents = req.subtotal_cents + req.tax_cents + req.shipping_cents

    # 2. Load authoritative business profile & invoices
    bp_res = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    bp = bp_res.scalar_one_or_none()
    if not bp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business profile for user '{user_id}' not found.",
        )

    inv_res = await db.execute(
        select(Invoice).where(Invoice.business_profile_id == bp.id)
    )
    invoices = inv_res.scalars().all()

    # 3. GATE A — Deterministic Affordability Verification
    affordability = check_business_affordability(bp, invoices, proposed_amount_cents=total_cents)
    if not affordability.is_affordable:
        # Fails closed on safety reserve breach
        db.add(
            AuditEvent(
                timestamp=now_utc,
                event_type=AuditEventType.SECURITY_BLOCK,
                actor=user_id,
                role="BUSINESS_ADMIN",
                module="BUSINESS",
                summary=f"Gate A Affordability Denial: Purchase of ${total_cents / 100:,.2f} rejected. {affordability.reason}",
                entity_id=None,
                decision="DENY",
                details={
                    "total_cents": total_cents,
                    "proposed_merchant": req.merchant_name,
                    "affordability": affordability.model_dump(),
                },
                severity=SeverityLevel.ALERT,
            )
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Affordability violation (Gate A): {affordability.reason}",
        )

    # 4. Generate proposal and line items
    proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
    items_list: List[Dict[str, Any]] = []

    proposal = PurchaseProposal(
        id=proposal_id,
        user_id=user_id,
        care_profile_id=None, # Isolation invariant: NEVER associate care profile with business
        module=ModuleScope.BUSINESS,
        title=req.title.strip(),
        merchant_id=req.merchant_id.strip(),
        merchant_name=req.merchant_name.strip(),
        subtotal_cents=req.subtotal_cents,
        tax_cents=req.tax_cents,
        shipping_cents=req.shipping_cents,
        total_cents=total_cents,
        currency=req.currency.upper(),
        ai_reasoning=req.ai_reasoning.strip(),
        state=TransactionLifecycleState.PROPOSED,
    )
    db.add(proposal)
    await db.flush()

    for item in req.items:
        it_id = f"item-{uuid.uuid4().hex[:8]}"
        item_total = item.quantity * item.unit_price_cents
        pi = ProposalItem(
            id=it_id,
            proposal_id=proposal_id,
            product_id=item.product_id or f"prod-{uuid.uuid4().hex[:6]}",
            name=item.name.strip(),
            sku=item.sku,
            category=item.category or "General",
            quantity=item.quantity,
            unit_price_cents=item.unit_price_cents,
            total_cents=item_total,
        )
        db.add(pi)
        items_list.append({
            "product_id": pi.product_id,
            "name": pi.name,
            "sku": pi.sku,
            "category": pi.category,
            "quantity": pi.quantity,
            "unit_price_cents": pi.unit_price_cents,
            "total_cents": pi.total_cents,
        })

    # 5. Cryptographic Transaction Fingerprint Binding
    canonical_json, sha_hash = compute_transaction_fingerprint(
        user_id=user_id,
        module=ModuleScope.BUSINESS.value,
        merchant_id=req.merchant_id.strip(),
        merchant_name=req.merchant_name.strip(),
        items=items_list,
        total_cents=total_cents,
        currency=req.currency.upper(),
        subtotal_cents=req.subtotal_cents,
        tax_cents=req.tax_cents,
        shipping_cents=req.shipping_cents,
        care_profile_id=None,
        policy_version=1,
    )

    fp_record = TransactionFingerprint(
        id=f"fp-{uuid.uuid4().hex[:8]}",
        proposal_id=proposal_id,
        canonical_payload=canonical_json,
        sha256_hash=sha_hash,
        policy_version=1,
    )
    db.add(fp_record)

    # 6. GATE B — Universal Trust Engine Policy Evaluation against pol-003
    pol_res = await db.execute(
        select(ConsentPolicy).where(
            ConsentPolicy.user_id == user_id,
            ConsentPolicy.scope == ModuleScope.BUSINESS,
        ).order_by(desc(ConsentPolicy.policy_version))
    )
    consent_policy = pol_res.scalars().first()
    policy_dict = (
        {
            "max_per_order_cents": consent_policy.max_per_order_cents,
            "monthly_budget_cents": consent_policy.monthly_budget_cents,
            "monthly_spent_cents": consent_policy.monthly_spent_cents,
            "currency": consent_policy.currency,
            "allowed_merchants": consent_policy.allowed_merchants,
            "allowed_categories": consent_policy.allowed_categories,
            "approval_mode": consent_policy.approval_mode,
            "valid_until": consent_policy.valid_until,
            "is_revoked": consent_policy.is_revoked,
            "policy_version": consent_policy.policy_version,
        }
        if consent_policy
        else None
    )

    eval_result = evaluate_transaction_policy(
        user_id=user_id,
        module=ModuleScope.BUSINESS,
        merchant_id=req.merchant_id.strip(),
        merchant_name=req.merchant_name.strip(),
        items=items_list,
        total_cents=total_cents,
        currency=req.currency.upper(),
        fingerprint_hash=sha_hash,
        computed_fingerprint_hash=sha_hash,
        policy=policy_dict,
        acting_user_id=user_id,
        existing_approval=None,
    )

    eval_record = PolicyEvaluationRecord(
        id=f"pe-{uuid.uuid4().hex[:8]}",
        proposal_id=proposal_id,
        decision=eval_result.decision,
        rules_evaluated=eval_result.rules_evaluated,
        passed_checks=eval_result.passed_checks,
        failed_checks=eval_result.failed_checks,
        human_explanation=eval_result.human_explanation,
        fingerprint_hash=sha_hash,
        policy_version=consent_policy.policy_version if consent_policy else 1,
    )
    db.add(eval_record)

    # 7. Apply Lifecycle Transition
    if eval_result.decision == PolicyDecision.DENY:
        proposal.state = TransactionLifecycleState.REJECTED
    elif eval_result.decision == PolicyDecision.REQUIRE_APPROVAL:
        proposal.state = TransactionLifecycleState.AWAITING_APPROVAL
    else:
        # Invariant: BUSINESS module always requires explicit human approval
        proposal.state = TransactionLifecycleState.AWAITING_APPROVAL

    # 8. Record Audit Events
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.PROPOSAL_CREATED,
            actor=user_id,
            role="BUSINESS_ADMIN",
            module="BUSINESS",
            summary=f"Created Business Proposal #{proposal_id} for {req.title} (${total_cents / 100:,.2f})",
            entity_id=proposal_id,
            details={"total_cents": total_cents, "merchant": req.merchant_name},
            severity=SeverityLevel.INFO,
        )
    )
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.FINGERPRINT_GENERATED,
            actor="TRUST_ENGINE",
            role="SYSTEM",
            module="BUSINESS",
            summary=f"Bound canonical SHA-256 fingerprint: {sha_hash[:16]}...",
            entity_id=proposal_id,
            fingerprint=sha_hash,
            details={"policy_version": 1},
            severity=SeverityLevel.INFO,
        )
    )
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.POLICY_EVALUATED,
            actor="TRUST_ENGINE",
            role="SYSTEM",
            module="BUSINESS",
            summary=f"Policy evaluated for Business Proposal #{proposal_id}: {eval_result.decision.value}",
            entity_id=proposal_id,
            decision=eval_result.decision.value,
            fingerprint=sha_hash,
            details={
                "decision": eval_result.decision.value,
                "passed_checks": eval_result.passed_checks,
                "failed_checks": eval_result.failed_checks,
            },
            severity=SeverityLevel.INFO if eval_result.decision != PolicyDecision.DENY else SeverityLevel.WARNING,
        )
    )

    await db.commit()

    # Reload proposal with items and fingerprint for response
    q_reload = (
        select(PurchaseProposal)
        .where(PurchaseProposal.id == proposal_id)
        .options(
            selectinload(PurchaseProposal.items),
            selectinload(PurchaseProposal.fingerprint),
        )
    )
    res_reload = await db.execute(q_reload)
    reloaded_proposal = res_reload.scalar_one()

    return to_proposal_response(
        reloaded_proposal,
        tx=None,
        ev=eval_record,
        fingerprint_hash=sha_hash,
    )
