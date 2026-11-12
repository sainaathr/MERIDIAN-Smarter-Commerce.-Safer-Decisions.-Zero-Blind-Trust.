from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from app.models.enums import (
    TransactionLifecycleState,
    ModuleScope,
    PolicyDecision,
    ApprovalMode,
    ApprovalType,
    AuditEventType,
    SeverityLevel,
)
from app.payments.schemas import PayPalOrderResponse

# Line Item Schemas
class ProposalItemCreate(BaseModel):
    product_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    sku: Optional[str] = None
    category: Optional[str] = "General"
    quantity: int = Field(..., gt=0)
    unit_price_cents: int = Field(..., ge=0)

    model_config = ConfigDict(from_attributes=True)

class ProposalItemResponse(ProposalItemCreate):
    id: str
    total_cents: int

# Proposal Schemas
class ProposalCreate(BaseModel):
    user_id: str
    care_profile_id: Optional[str] = None
    module: ModuleScope
    title: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    merchant_name: str = Field(..., min_length=1)
    items: List[ProposalItemCreate] = Field(..., min_length=1)
    subtotal_cents: int = Field(..., ge=0)
    tax_cents: int = Field(0, ge=0)
    shipping_cents: int = Field(0, ge=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    ai_reasoning: str = Field(..., min_length=1)

    model_config = ConfigDict(from_attributes=True)

class ProposalResponse(BaseModel):
    id: str
    user_id: str
    care_profile_id: Optional[str] = None
    module: ModuleScope
    title: str
    merchant_id: str
    merchant_name: str
    items: List[ProposalItemResponse]
    subtotal_cents: int
    tax_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    ai_reasoning: str
    state: TransactionLifecycleState
    version: int = 1
    created_at: datetime
    fingerprint_sha256: Optional[str] = None
    policy_decision: Optional[str] = None
    approval_status: Optional[str] = None
    requires_approval: bool = False
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    paypal_status: Optional[str] = None
    is_simulated: Optional[bool] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

# Canonical Fingerprint Schema
class FingerprintRequest(BaseModel):
    user_id: str
    care_profile_id: Optional[str] = None
    module: ModuleScope
    merchant_id: str
    merchant_name: str
    items: List[ProposalItemCreate]
    total_cents: int
    currency: str
    policy_version: int = 1

class FingerprintResponse(BaseModel):
    canonical_payload: str
    sha256_hash: str
    policy_version: int

# Policy Evaluation Schemas
class PolicyEvaluationRequest(BaseModel):
    proposal_id: Optional[str] = None
    proposal: Optional[ProposalCreate] = None
    acting_user_id: str

class PolicyEvaluationResponse(BaseModel):
    decision: PolicyDecision
    rules_evaluated: List[str]
    passed_checks: List[str]
    failed_checks: List[str]
    human_explanation: str
    fingerprint_hash: str
    policy_version: int
    timestamp: datetime
    requires_approval: bool

# Approval Schemas
class ApprovalCreate(BaseModel):
    proposal_id: str
    fingerprint_hash: str
    approver_user_id: str
    approval_type: ApprovalType = ApprovalType.USER_DIRECT
    consent_policy_id: Optional[str] = None
    ttl_seconds: int = Field(86400, gt=0) # 24 hours default

class ApprovalResponse(BaseModel):
    id: str
    fingerprint_hash: str
    approver_user_id: str
    approval_type: ApprovalType
    consent_policy_id: Optional[str]
    expires_at: datetime
    is_revoked: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# State Transition Schema
class StateTransitionRequest(BaseModel):
    target_state: TransactionLifecycleState
    actor_id: str
    actor_role: str
    reason: Optional[str] = None
    expected_version: Optional[int] = None

class StateTransitionResponse(BaseModel):
    success: bool
    proposal_id: str
    previous_state: TransactionLifecycleState
    new_state: TransactionLifecycleState
    timestamp: datetime

# Consent Policy Schemas
class ConsentPolicyCreate(BaseModel):
    user_id: str
    scope: ModuleScope
    title: str
    max_per_order_cents: int = Field(..., gt=0)
    monthly_budget_cents: int = Field(..., gt=0)
    currency: str = "USD"
    allowed_merchants: List[str] = Field(default_factory=list)
    allowed_categories: List[str] = Field(default_factory=list)
    approval_mode: ApprovalMode = ApprovalMode.EXPLICIT_ALWAYS
    valid_until: datetime

class ConsentPolicyResponse(BaseModel):
    id: str
    user_id: str
    scope: ModuleScope
    title: str
    max_per_order_cents: int
    monthly_budget_cents: int
    monthly_spent_cents: int
    currency: str
    allowed_merchants: List[str]
    allowed_categories: List[str]
    approval_mode: ApprovalMode
    valid_until: datetime
    is_revoked: bool
    policy_version: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Audit Event Schema
class AuditEventResponse(BaseModel):
    id: str
    timestamp: datetime
    event_type: AuditEventType
    actor: str
    role: str
    module: str
    summary: str
    entity_id: Optional[str]
    previous_state: Optional[str]
    new_state: Optional[str]
    decision: Optional[str]
    fingerprint: Optional[str]
    details: Dict[str, Any]
    severity: SeverityLevel

    model_config = ConfigDict(from_attributes=True)

# ==============================================================================
# Care (Assisted Commerce) Schemas
# ==============================================================================

class CareItemResponse(BaseModel):
    id: str
    name: str
    category: str
    frequency: str
    days_remaining: int
    estimated_cost: float
    merchant: str
    auto_reorder_eligible: bool
    last_ordered: str

    model_config = ConfigDict(from_attributes=True)

class GuardianResponse(BaseModel):
    id: str
    name: str
    role: str
    email: str
    can_approve: bool
    max_approval_amount: float

    model_config = ConfigDict(from_attributes=True)

class CareProfileResponse(BaseModel):
    id: str
    user_id: str
    recipient_name: str
    relationship: str
    approval_mode: str
    monthly_budget: float
    monthly_spent: float
    reserved_amount: float = 0.0
    simulated_monthly_spent: float = 0.0
    currency: str
    guardians: List[GuardianResponse] = Field(default_factory=list)
    monitored_items: List[CareItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

class CareModeUpdateRequest(BaseModel):
    care_profile_id: Optional[str] = None
    mode: str = Field(..., description="MODE_A_EVERY_ORDER or MODE_B_CONTROLLED_AUTO")

class CareReorderRequest(BaseModel):
    schedule_id: str
    care_profile_id: Optional[str] = None
    actor_id: Optional[str] = "usr-alex-01"

class CareReorderResponse(BaseModel):
    proposal: ProposalResponse
    requires_approval: bool
    decision: PolicyDecision
    human_explanation: str
    order: Optional[PayPalOrderResponse] = None

    model_config = ConfigDict(from_attributes=True)

# ==============================================================================
# Business / FLOW Intelligence Schemas
# ==============================================================================

class BusinessProfileResponse(BaseModel):
    id: str
    user_id: str
    company_name: str
    operating_cash_cents: int
    operating_cash: float
    minimum_safety_reserve_cents: int
    minimum_safety_reserve: float
    currency: str
    estimated_runway_days: Optional[int] = None
    total_unpaid_receivables_cents: int = 0
    total_unpaid_payables_cents: int = 0
    projected_net_liquidity_cents: int = 0

    model_config = ConfigDict(from_attributes=True)

class InvoiceResponse(BaseModel):
    id: str
    business_profile_id: str
    counterparty: str
    invoice_type: str # RECEIVABLE or PAYABLE
    amount_cents: int
    amount: float
    due_date: datetime
    status: str # PENDING, PAID, OVERDUE, CANCELLED
    category: str

    model_config = ConfigDict(from_attributes=True)

class InvoiceCreateRequest(BaseModel):
    counterparty: str = Field(..., min_length=1, max_length=255)
    invoice_type: str = Field(..., description="RECEIVABLE or PAYABLE")
    amount_cents: int = Field(..., gt=0, description="Amount in positive integer minor units (cents)")
    due_date: datetime = Field(..., description="Due date of invoice with timezone")
    category: str = Field(..., min_length=1, max_length=100)
    status: Optional[str] = Field("PENDING", description="Initial invoice status")

    @field_validator("invoice_type")
    @classmethod
    def validate_invoice_type(cls, v: str) -> str:
        v_upper = v.strip().upper()
        if v_upper not in ("RECEIVABLE", "PAYABLE"):
            raise ValueError("invoice_type must be either 'RECEIVABLE' or 'PAYABLE'")
        return v_upper

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> str:
        if not v:
            return "PENDING"
        v_upper = v.strip().upper()
        if v_upper != "PENDING":
            raise ValueError(
                f"Initial invoice status must be 'PENDING'. Non-pending status '{v}' is rejected "
                "to prevent unauthorized manipulation of cashflow projections."
            )
        return "PENDING"

class CashflowForecastItem(BaseModel):
    day: str # e.g. "Nov 10"
    date: str # YYYY-MM-DD
    projected_balance_cents: int
    projected_balance: float
    payables_cents: int
    receivables_cents: int
    net_change_cents: int

class CashflowForecastResponse(BaseModel):
    starting_balance_cents: int
    starting_balance: float
    minimum_safety_reserve_cents: int
    minimum_safety_reserve: float
    total_receivables_cents: int
    total_payables_cents: int
    projected_minimum_cash_cents: int
    safety_reserve_margin_cents: int
    estimated_runway_days: int
    currency: str
    forecast_days: List[CashflowForecastItem]
    assumptions: List[str] = Field(default_factory=list)

class BusinessProposalCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    merchant_id: str = Field(..., min_length=1, max_length=100)
    merchant_name: str = Field(..., min_length=1, max_length=255)
    items: List[ProposalItemCreate] = Field(..., min_length=1)
    subtotal_cents: int = Field(..., gt=0)
    tax_cents: int = Field(0, ge=0)
    shipping_cents: int = Field(0, ge=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    ai_reasoning: str = Field(..., min_length=1)

class BusinessAffordabilityCheckResponse(BaseModel):
    is_affordable: bool
    reason: str
    starting_cash_cents: int
    minimum_safety_reserve_cents: int
    projected_minimum_cash_cents: int
    safety_reserve_margin_cents: int
    proposal_amount_cents: int
    violates_safety_reserve: bool

# ==============================================================================
# Collective & Shared Purchasing (COMMON) Schemas
# ==============================================================================

class GroupParticipantResponse(BaseModel):
    id: str
    group_purchase_id: str
    user_id: Optional[str] = None
    name: str
    email: str
    share_cents: int
    share_amount: float
    status: str
    is_organizer: bool

class GroupPurchaseResponse(BaseModel):
    id: str
    organizer_user_id: str
    title: str
    target_item: str
    total_target_cents: int
    total_target: float
    amount_collected_cents: int
    amount_collected: float
    total_committed_cents: int
    total_committed: float
    remaining_cents: int
    remaining_amount: float
    currency: str
    status: str
    deadline: datetime
    participants: List[GroupParticipantResponse]
    participant_count: int
    progress_percentage: int

class GroupPurchaseCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    target_item: str = Field(..., min_length=3, max_length=255)
    total_target_cents: int = Field(..., gt=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    deadline_days: int = Field(7, gt=0, le=90)
    organizer_name: Optional[str] = Field("Alex Rivera", min_length=1, max_length=255)
    organizer_email: Optional[str] = Field("alex.rivera@demo.meridian", min_length=3, max_length=255)
    organizer_share_cents: int = Field(0, ge=0)

class GroupJoinRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)
    share_cents: int

class GroupCommitmentUpdateRequest(BaseModel):
    share_cents: int
