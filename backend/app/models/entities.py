import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    String,
    Integer,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.enums import (
    UserRole,
    TransactionLifecycleState,
    ModuleScope,
    PolicyDecision,
    ApprovalMode,
    ApprovalType,
    AuditEventType,
    SeverityLevel,
    ReservationStatus,
)

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def generate_uuid() -> str:
    return str(uuid.uuid4())

# ==============================================================================
# 1. Identity & Guardianship
# ==============================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(50), default=UserRole.CONSUMER, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    care_profiles: Mapped[List["CareProfile"]] = relationship("CareProfile", back_populates="recipient_user", cascade="all, delete-orphan")
    consent_policies: Mapped[List["ConsentPolicy"]] = relationship("ConsentPolicy", back_populates="user", cascade="all, delete-orphan")
    proposals: Mapped[List["PurchaseProposal"]] = relationship("PurchaseProposal", back_populates="user", cascade="all, delete-orphan")

class CareProfile(Base):
    __tablename__ = "care_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_mode: Mapped[ApprovalMode] = mapped_column(String(50), default=ApprovalMode.CONTROLLED_AUTO, nullable=False)
    monthly_budget_cents: Mapped[int] = mapped_column(Integer, default=25000, nullable=False) # $250.00
    monthly_spent_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    simulated_monthly_spent_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    notes_redacted: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # Mock synthetic instructions only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    recipient_user: Mapped["User"] = relationship("User", back_populates="care_profiles")
    guardians: Mapped[List["GuardianRelationship"]] = relationship("GuardianRelationship", back_populates="care_profile", cascade="all, delete-orphan")
    reorder_schedules: Mapped[List["ReorderSchedule"]] = relationship("ReorderSchedule", back_populates="care_profile", cascade="all, delete-orphan")

class GuardianRelationship(Base):
    __tablename__ = "guardian_relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    care_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("care_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    guardian_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. "Primary Financial Guardian"
    can_approve: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_approval_amount_cents: Mapped[int] = mapped_column(Integer, default=15000, nullable=False) # $150.00 ceiling
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    care_profile: Mapped["CareProfile"] = relationship("CareProfile", back_populates="guardians")
    guardian_user: Mapped["User"] = relationship("User")
    permissions: Mapped[List["DelegatedPermission"]] = relationship("DelegatedPermission", back_populates="guardian_rel", cascade="all, delete-orphan")

class DelegatedPermission(Base):
    __tablename__ = "delegated_permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    guardian_relationship_id: Mapped[str] = mapped_column(String(36), ForeignKey("guardian_relationships.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. "Medical Supplies"
    max_per_order_cents: Mapped[int] = mapped_column(Integer, default=7500, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    guardian_rel: Mapped["GuardianRelationship"] = relationship("GuardianRelationship", back_populates="permissions")

class ConsentPolicy(Base):
    __tablename__ = "consent_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope: Mapped[ModuleScope] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    max_per_order_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_budget_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_spent_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    simulated_monthly_spent_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    allowed_merchants: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_categories: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    approval_mode: Mapped[ApprovalMode] = mapped_column(String(50), default=ApprovalMode.EXPLICIT_ALWAYS, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="consent_policies")
    approvals: Mapped[List["Approval"]] = relationship("Approval", back_populates="consent_policy")

# ==============================================================================
# 2. Commerce & Proposals
# ==============================================================================

class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(100), ForeignKey("merchants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

class PurchaseProposal(Base):
    __tablename__ = "purchase_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    care_profile_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("care_profiles.id"), nullable=True)
    module: Mapped[ModuleScope] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    ai_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[TransactionLifecycleState] = mapped_column(String(50), default=TransactionLifecycleState.DRAFT, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="proposals")
    items: Mapped[List["ProposalItem"]] = relationship("ProposalItem", back_populates="proposal", cascade="all, delete-orphan")
    fingerprint: Mapped[Optional["TransactionFingerprint"]] = relationship("TransactionFingerprint", back_populates="proposal", uselist=False, cascade="all, delete-orphan")
    evaluations: Mapped[List["PolicyEvaluationRecord"]] = relationship("PolicyEvaluationRecord", back_populates="proposal", cascade="all, delete-orphan")
    transactions: Mapped[List["Transaction"]] = relationship("Transaction", back_populates="proposal", cascade="all, delete-orphan")

class ProposalItem(Base):
    __tablename__ = "proposal_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase_proposals.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    proposal: Mapped["PurchaseProposal"] = relationship("PurchaseProposal", back_populates="items")

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase_proposals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    state: Mapped[TransactionLifecycleState] = mapped_column(String(50), default=TransactionLifecycleState.DRAFT, nullable=False, index=True)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    capture_idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    paypal_order_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True, nullable=True)
    paypal_capture_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True, nullable=True)
    paypal_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    paypal_approval_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reconciliation_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    first_dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    proposal: Mapped["PurchaseProposal"] = relationship("PurchaseProposal", back_populates="transactions")
    items: Mapped[List["TransactionItem"]] = relationship("TransactionItem", back_populates="transaction", cascade="all, delete-orphan")

class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    transaction: Mapped["Transaction"] = relationship("Transaction", back_populates="items")

# ==============================================================================
# 3. Trust, Approvals & Policy Evaluation
# ==============================================================================

class TransactionFingerprint(Base):
    __tablename__ = "transaction_fingerprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase_proposals.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    canonical_payload: Mapped[str] = mapped_column(Text, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    proposal: Mapped["PurchaseProposal"] = relationship("PurchaseProposal", back_populates="fingerprint")
    approvals: Mapped[List["Approval"]] = relationship("Approval", primaryjoin="foreign(Approval.fingerprint_hash) == TransactionFingerprint.sha256_hash", uselist=True)

class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    approver_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    approval_type: Mapped[ApprovalType] = mapped_column(String(50), default=ApprovalType.USER_DIRECT, nullable=False)
    consent_policy_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("consent_policies.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    approver: Mapped["User"] = relationship("User")
    consent_policy: Mapped[Optional["ConsentPolicy"]] = relationship("ConsentPolicy", back_populates="approvals")

class PolicyEvaluationRecord(Base):
    __tablename__ = "policy_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase_proposals.id", ondelete="CASCADE"), nullable=False, index=True)
    decision: Mapped[PolicyDecision] = mapped_column(String(50), nullable=False)
    rules_evaluated: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    passed_checks: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    failed_checks: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    human_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    proposal: Mapped["PurchaseProposal"] = relationship("PurchaseProposal", back_populates="evaluations")

class BudgetReservation(Base):
    __tablename__ = "budget_reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase_proposals.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    care_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("care_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    consent_policy_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("consent_policies.id"), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(String(50), default=ReservationStatus.HELD, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    proposal: Mapped["PurchaseProposal"] = relationship("PurchaseProposal")
    care_profile: Mapped["CareProfile"] = relationship("CareProfile")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    event_type: Mapped[AuditEventType] = mapped_column(String(50), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    previous_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    severity: Mapped[SeverityLevel] = mapped_column(String(20), default=SeverityLevel.INFO, nullable=False)

# ==============================================================================
# 4. Future Modules Readiness (Business, Collective, Care Schedules)
# ==============================================================================

class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operating_cash_cents: Mapped[int] = mapped_column(Integer, default=1420000, nullable=False) # $14,200.00
    minimum_safety_reserve_cents: Mapped[int] = mapped_column(Integer, default=800000, nullable=False) # $8,000.00
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    business_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("business_profiles.id"), nullable=False, index=True)
    counterparty: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_type: Mapped[str] = mapped_column(String(20), nullable=False) # RECEIVABLE or PAYABLE
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)

class GroupPurchase(Base):
    __tablename__ = "group_purchases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    organizer_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_item: Mapped[str] = mapped_column(String(255), nullable=False)
    total_target_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_collected_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    participants: Mapped[List["GroupParticipant"]] = relationship("GroupParticipant", back_populates="group", cascade="all, delete-orphan")

class GroupParticipant(Base):
    __tablename__ = "group_participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    group_purchase_id: Mapped[str] = mapped_column(String(36), ForeignKey("group_purchases.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    share_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False) # PENDING, COMMITTED, PAID
    is_organizer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    group: Mapped["GroupPurchase"] = relationship("GroupPurchase", back_populates="participants")

class ReorderSchedule(Base):
    __tablename__ = "reorder_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    care_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("care_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    preferred_merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    days_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_reorder_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    care_profile: Mapped["CareProfile"] = relationship("CareProfile", back_populates="reorder_schedules")
