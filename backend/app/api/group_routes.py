"""
MERIDIAN — Collective & Shared Purchasing (COMMON) API Router
Governs multi-party group purchasing campaigns with strict financial integrity,
authoritative server-side calculation, and multi-party commitment isolation.

Invariants Enforced:
1. "AI proposes. Deterministic policy authorizes."
2. Participants authorize only their individual share; organizers cannot debit participant wallets.
3. Commitments are strictly distinguished from actual payments (zero client-side payment state forgery).
4. Monetary values are strictly calculated in minor units (cents) from persisted records.
5. Duplicate memberships are rejected with HTTP 409 Conflict.
6. Group mutations require verified organizer/participant ownership checks (HTTP 403 Forbidden).
7. Zero CARE budget reservations are created (module isolation).
"""

from datetime import datetime, timezone, timedelta
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.entities import (
    GroupPurchase,
    GroupParticipant,
    User,
    AuditEvent,
    ConsentPolicy,
)
from app.models.enums import (
    ModuleScope,
    AuditEventType,
    SeverityLevel,
    PolicyDecision,
    ApprovalMode,
)
from app.schemas.domain import (
    GroupPurchaseResponse,
    GroupParticipantResponse,
    GroupPurchaseCreateRequest,
    GroupJoinRequest,
    GroupCommitmentUpdateRequest,
    PolicyEvaluationResponse,
)
from app.engine.policy import evaluate_transaction_policy
from app.engine.fingerprint import compute_transaction_fingerprint

router = APIRouter(prefix="/groups", tags=["Collective & Shared Purchasing (COMMON)"])


def to_str(val: any) -> str:
    if val is None:
        return ""
    return val.value if hasattr(val, "value") else str(val)


def build_group_response(group: GroupPurchase) -> GroupPurchaseResponse:
    """Computes authoritative financial totals and progress from persisted participant records."""
    participants_resp: List[GroupParticipantResponse] = []
    total_paid_cents = 0
    total_committed_cents = 0

    for p in group.participants:
        share = int(p.share_cents)
        p_status = (p.status or "PENDING").upper()
        if p_status == "PAID":
            total_paid_cents += share
            total_committed_cents += share
        elif p_status == "COMMITTED":
            total_committed_cents += share

        participants_resp.append(
            GroupParticipantResponse(
                id=p.id,
                group_purchase_id=p.group_purchase_id,
                user_id=p.user_id,
                name=p.name,
                email=p.email,
                share_cents=share,
                share_amount=round(share / 100.0, 2),
                status=p_status,
                is_organizer=bool(p.is_organizer),
            )
        )

    target_cents = int(group.total_target_cents)
    remaining_cents = max(0, target_cents - total_paid_cents)
    progress_pct = min(100, int((total_committed_cents / target_cents) * 100)) if target_cents > 0 else 0

    # Effective status: if funded by commitments, flag FUNDED if still OPEN
    effective_status = group.status
    if effective_status == "OPEN" and total_committed_cents >= target_cents:
        effective_status = "FUNDED"

    return GroupPurchaseResponse(
        id=group.id,
        organizer_user_id=group.organizer_user_id,
        title=group.title,
        target_item=group.target_item,
        total_target_cents=target_cents,
        total_target=round(target_cents / 100.0, 2),
        amount_collected_cents=total_paid_cents, # Authoritative payment total from verified records
        amount_collected=round(total_paid_cents / 100.0, 2),
        total_committed_cents=total_committed_cents,
        total_committed=round(total_committed_cents / 100.0, 2),
        remaining_cents=remaining_cents,
        remaining_amount=round(remaining_cents / 100.0, 2),
        currency=group.currency or "USD",
        status=effective_status,
        deadline=group.deadline,
        participants=participants_resp,
        participant_count=len(participants_resp),
        progress_percentage=progress_pct,
    )


# ==============================================================================
# 1. List and Retrieve Group Purchases
# ==============================================================================

@router.get("", response_model=List[GroupPurchaseResponse])
async def list_groups(
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Lists group purchasing campaigns visible to the requesting user.
    Includes groups organized by the user, groups the user has joined, and active public groups.
    """
    stmt = (
        select(GroupPurchase)
        .options(selectinload(GroupPurchase.participants))
        .order_by(desc(GroupPurchase.deadline))
    )
    res = await db.execute(stmt)
    all_groups = res.scalars().all()

    # Filter to visible groups
    visible: List[GroupPurchaseResponse] = []
    for g in all_groups:
        is_organizer = g.organizer_user_id == user_id
        is_member = any(p.user_id == user_id for p in g.participants)
        is_open = g.status == "OPEN"
        if is_organizer or is_member or is_open:
            visible.append(build_group_response(g))

    return visible


@router.get("/{group_id}", response_model=GroupPurchaseResponse)
async def get_group_details(
    group_id: str,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves detailed group campaign data including participant commitments,
    verified collected funds, and funding progress.
    """
    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group_id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    group = res.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group purchase '{group_id}' not found.",
        )

    # Authorization / Visibility check
    is_organizer = group.organizer_user_id == user_id
    is_member = any(p.user_id == user_id for p in group.participants)
    is_open = group.status == "OPEN"
    if not (is_organizer or is_member or is_open):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: You are not authorized to view group '{group_id}'.",
        )

    return build_group_response(group)


# ==============================================================================
# 2. Create Group Purchase Campaign
# ==============================================================================

@router.post("", response_model=GroupPurchaseResponse, status_code=status.HTTP_201_CREATED)
async def create_group_purchase(
    req: GroupPurchaseCreateRequest,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new collective group purchasing campaign.
    The creator is registered as the initial organizer participant.
    """
    now_utc = datetime.now(timezone.utc)

    if req.total_target_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Total target amount must be strictly greater than zero.",
        )

    if req.organizer_share_cents < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organizer share amount cannot be negative.",
        )

    if req.organizer_share_cents > req.total_target_cents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organizer share cannot exceed the total campaign target.",
        )

    deadline_utc = now_utc + timedelta(days=req.deadline_days)
    group_id = f"col-{uuid.uuid4().hex[:8]}"

    group = GroupPurchase(
        id=group_id,
        organizer_user_id=user_id,
        title=req.title.strip(),
        target_item=req.target_item.strip(),
        total_target_cents=req.total_target_cents,
        amount_collected_cents=0, # Commitments are distinct from payments
        currency=req.currency.upper(),
        status="OPEN",
        deadline=deadline_utc,
    )
    db.add(group)
    await db.flush()

    # Automatically register organizer as initial participant
    org_status = "COMMITTED" if req.organizer_share_cents > 0 else "PENDING"
    organizer_part = GroupParticipant(
        id=f"part-{uuid.uuid4().hex[:8]}",
        group_purchase_id=group.id,
        user_id=user_id,
        name=req.organizer_name.strip() if req.organizer_name else "Alex Rivera (Organizer)",
        email=req.organizer_email.strip().lower() if req.organizer_email else "alex.rivera@demo.meridian",
        share_cents=req.organizer_share_cents,
        status=org_status,
        is_organizer=True,
    )
    db.add(organizer_part)
    await db.flush()

    # Audit Trail
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.PROPOSAL_CREATED,
            actor=user_id,
            role="ORGANIZER",
            module="COLLECTIVE",
            summary=(
                f"Created Collective Campaign #{group.id}: '{req.title}' "
                f"(Target: ${req.total_target_cents / 100:,.2f} {req.currency.upper()})"
            ),
            entity_id=group.id,
            details={
                "total_target_cents": req.total_target_cents,
                "target_item": req.target_item,
                "deadline": deadline_utc.isoformat(),
                "organizer_share_cents": req.organizer_share_cents,
            },
            severity=SeverityLevel.INFO,
        )
    )

    await db.commit()

    # Reload with participants
    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group.id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    loaded = res.scalar_one()

    return build_group_response(loaded)


# ==============================================================================
# 3. Join Group Purchase Campaign & Submit Commitment
# ==============================================================================

@router.post("/{group_id}/join", response_model=GroupPurchaseResponse, status_code=status.HTTP_201_CREATED)
async def join_group_purchase(
    group_id: str,
    req: GroupJoinRequest,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Enables a user to join an active group campaign with an explicit financial commitment.
    Strictly distinguishes commitment pledges from actual payment operations.
    Fails closed on duplicate memberships or closed/expired campaigns.
    """
    now_utc = datetime.now(timezone.utc)

    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group_id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    group = res.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group purchase '{group_id}' not found.",
        )

    # Invariant: Group must be OPEN
    if group.status != "OPEN":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot join group in '{group.status}' state. Only OPEN groups accept new participants.",
        )

    # Invariant: Deadline validation
    group_deadline = group.deadline
    if group_deadline and group_deadline.tzinfo is None:
        group_deadline = group_deadline.replace(tzinfo=timezone.utc)
    if group_deadline and now_utc > group_deadline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot join group: The campaign deadline has passed.",
        )

    # Invariant: Positive commitment amount
    if req.share_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Participant commitment amount must be strictly greater than zero.",
        )

    # Invariant: Duplicate membership prevention
    clean_email = req.email.strip().lower()
    for existing_p in group.participants:
        if (existing_p.email and existing_p.email.lower() == clean_email) or (
            user_id and existing_p.user_id == user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User '{clean_email}' is already a participant in group '{group_id}'.",
            )

    # Invariant: Commitment is distinct from Payment. Status is COMMITTED, NEVER PAID by client assertion.
    participant = GroupParticipant(
        id=f"part-{uuid.uuid4().hex[:8]}",
        group_purchase_id=group.id,
        user_id=user_id,
        name=req.name.strip(),
        email=clean_email,
        share_cents=req.share_cents,
        status="COMMITTED", # Pledged commitment
        is_organizer=False,
    )
    db.add(participant)
    await db.flush()

    # Re-evaluate total commitments
    group.participants.append(participant)
    total_committed = sum(p.share_cents for p in group.participants if (p.status or "").upper() in ("COMMITTED", "PAID"))
    if total_committed >= group.total_target_cents:
        group.status = "FUNDED"

    # Audit Trail
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.POLICY_EVALUATED,
            actor=user_id or clean_email,
            role="PARTICIPANT",
            module="COLLECTIVE",
            summary=(
                f"Participant '{req.name.strip()}' committed ${req.share_cents / 100:,.2f} "
                f"to Collective Campaign #{group.id}."
            ),
            entity_id=group.id,
            details={
                "participant_id": participant.id,
                "share_cents": req.share_cents,
                "total_committed_cents": total_committed,
                "total_target_cents": group.total_target_cents,
            },
            severity=SeverityLevel.INFO,
        )
    )

    await db.commit()

    # Refresh
    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group.id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    loaded = res.scalar_one()

    return build_group_response(loaded)


# ==============================================================================
# 4. Update Participant Commitment
# ==============================================================================

@router.patch("/{group_id}/participants/{participant_id}", response_model=GroupPurchaseResponse)
async def update_participant_commitment(
    group_id: str,
    participant_id: str,
    req: GroupCommitmentUpdateRequest,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Updates an unfulfilled participant commitment amount.
    Enforces strict ownership: only the participant themselves or the organizer may adjust commitments.
    Cannot modify commitments that have already been PAID.
    """
    now_utc = datetime.now(timezone.utc)

    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group_id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    group = res.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group purchase '{group_id}' not found.",
        )

    if group.status not in ("OPEN", "FUNDED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update commitments on a group in '{group.status}' state.",
        )

    target_part: Optional[GroupParticipant] = None
    for p in group.participants:
        if p.id == participant_id:
            target_part = p
            break

    if not target_part:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Participant '{participant_id}' not found in group '{group_id}'.",
        )

    # Ownership & Authorization check:
    # Must be either the participant themselves or the group organizer
    is_owner = (target_part.user_id and target_part.user_id == user_id)
    is_organizer = (group.organizer_user_id == user_id)
    if not (is_owner or is_organizer):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You are not authorized to modify another participant's commitment.",
        )

    # Invariant: Cannot modify a PAID commitment
    if (target_part.status or "").upper() == "PAID":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify a commitment that has already been verified as PAID.",
        )

    if req.share_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Commitment amount must be strictly greater than zero.",
        )

    old_cents = target_part.share_cents
    target_part.share_cents = req.share_cents
    target_part.status = "COMMITTED"

    # Re-evaluate funding status
    total_committed = sum(p.share_cents for p in group.participants if (p.status or "").upper() in ("COMMITTED", "PAID"))
    if total_committed >= group.total_target_cents:
        group.status = "FUNDED"
    else:
        group.status = "OPEN"

    # Audit Trail
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.POLICY_EVALUATED,
            actor=user_id,
            role="PARTICIPANT" if is_owner else "ORGANIZER",
            module="COLLECTIVE",
            summary=(
                f"Updated commitment for participant '{target_part.name}' in Group #{group.id} "
                f"from ${old_cents / 100:,.2f} to ${req.share_cents / 100:,.2f}."
            ),
            entity_id=group.id,
            details={
                "participant_id": target_part.id,
                "old_share_cents": old_cents,
                "new_share_cents": req.share_cents,
                "total_committed_cents": total_committed,
            },
            severity=SeverityLevel.INFO,
        )
    )

    await db.commit()

    # Refresh
    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group.id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    loaded = res.scalar_one()

    return build_group_response(loaded)


# ==============================================================================
# 5. Deterministic Policy Evaluation (pol-004)
# ==============================================================================

@router.post("/{group_id}/evaluate-policy", response_model=PolicyEvaluationResponse)
async def evaluate_group_policy(
    group_id: str,
    user_id: Optional[str] = "usr-alex-01",
    db: AsyncSession = Depends(get_db),
):
    """
    Evaluates the group purchasing campaign against deterministic policy `pol-004`.
    Enforces Mode A (explicit human authorization required for collective orders).
    Preserves CARE isolation invariant: zero BudgetReservation is created.
    """
    now_utc = datetime.now(timezone.utc)

    stmt = (
        select(GroupPurchase)
        .where(GroupPurchase.id == group_id)
        .options(selectinload(GroupPurchase.participants))
    )
    res = await db.execute(stmt)
    group = res.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group purchase '{group_id}' not found.",
        )

    # Load pol-004 (COLLECTIVE)
    pol_stmt = (
        select(ConsentPolicy)
        .where(
            ConsentPolicy.user_id == user_id,
            ConsentPolicy.scope == ModuleScope.COLLECTIVE,
        )
        .order_by(desc(ConsentPolicy.policy_version))
    )
    pol_res = await db.execute(pol_stmt)
    policy = pol_res.scalars().first()

    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deterministic policy for scope 'COLLECTIVE' and user '{user_id}' not found.",
        )

    # Policy evaluation dictionary
    pol_dict = {
        "id": policy.id,
        "scope": to_str(policy.scope),
        "max_per_order_cents": policy.max_per_order_cents,
        "monthly_budget_cents": policy.monthly_budget_cents,
        "monthly_spent_cents": policy.monthly_spent_cents,
        "currency": policy.currency,
        "allowed_merchants": policy.allowed_merchants or [],
        "allowed_categories": policy.allowed_categories or [],
        "approval_mode": to_str(policy.approval_mode),
        "valid_until": policy.valid_until,
        "is_revoked": policy.is_revoked,
        "policy_version": policy.policy_version,
    }

    items_list = [
        {
            "product_id": "prod-group-target",
            "name": group.target_item,
            "category": "Studio Gear",
            "quantity": 1,
            "unit_price_cents": group.total_target_cents,
            "total_cents": group.total_target_cents,
        }
    ]

    canonical_json, sha_hash = compute_transaction_fingerprint(
        user_id=user_id,
        module=ModuleScope.COLLECTIVE.value,
        merchant_id="CameraHouse Demo",
        merchant_name="CameraHouse Demo",
        items=items_list,
        total_cents=group.total_target_cents,
        currency=group.currency,
        policy_version=policy.policy_version,
    )

    eval_result = evaluate_transaction_policy(
        user_id=user_id,
        module=ModuleScope.COLLECTIVE,
        merchant_id="CameraHouse Demo",
        merchant_name="CameraHouse Demo",
        items=items_list,
        total_cents=group.total_target_cents,
        currency=group.currency,
        fingerprint_hash=sha_hash,
        computed_fingerprint_hash=sha_hash,
        policy=pol_dict,
        acting_user_id=user_id,
        existing_approval=None,
    )

    decision_enum = PolicyDecision(eval_result.decision)

    # Audit Trail
    db.add(
        AuditEvent(
            timestamp=now_utc,
            event_type=AuditEventType.POLICY_EVALUATED,
            actor=user_id,
            role="ORGANIZER",
            module="COLLECTIVE",
            summary=(
                f"Evaluated policy 'pol-004' for Group #{group.id}: {decision_enum.value} "
                f"({eval_result.human_explanation})"
            ),
            entity_id=group.id,
            decision=decision_enum.value,
            details={
                "policy_id": policy.id,
                "passed_checks": eval_result.passed_checks,
                "failed_checks": eval_result.failed_checks,
            },
            severity=SeverityLevel.INFO if decision_enum != PolicyDecision.DENY else SeverityLevel.ALERT,
        )
    )

    await db.commit()

    return PolicyEvaluationResponse(
        decision=decision_enum,
        rules_evaluated=eval_result.rules_evaluated,
        passed_checks=eval_result.passed_checks,
        failed_checks=eval_result.failed_checks,
        human_explanation=eval_result.human_explanation,
        fingerprint_hash=sha_hash,
        policy_version=policy.policy_version,
        timestamp=now_utc,
        requires_approval=eval_result.requires_approval,
    )
