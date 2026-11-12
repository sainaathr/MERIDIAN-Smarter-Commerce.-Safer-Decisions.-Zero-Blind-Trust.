from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from app.models.enums import ModuleScope

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# ==============================================================================
# 1. Intent Extraction Schemas
# ==============================================================================
class IntentExtractionRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    user_id: Optional[str] = "usr-alex-01"
    module: Optional[ModuleScope] = ModuleScope.PERSONAL

class ShoppingIntentExtraction(BaseModel):
    intent_type: str = Field(..., description="Classification of user intent, e.g. PRODUCT_SEARCH, PURCHASE_INTENT, CLARIFICATION_NEEDED")
    module: ModuleScope = Field(ModuleScope.PERSONAL, description="Applicable commerce module")
    requested_products: List[str] = Field(default_factory=list, description="Extracted product names or descriptions")
    product_categories: List[str] = Field(default_factory=list, description="Identified product categories")
    preferences: List[str] = Field(default_factory=list, description="Explicit user preferences such as color, size, wireless")
    requested_quantity: Optional[int] = Field(None, gt=0, description="Quantity if explicitly mentioned")
    max_budget_cents: Optional[int] = Field(None, ge=0, description="Maximum budget in cents only if explicitly provided; None if absent")
    currency: Optional[str] = Field("USD", min_length=3, max_length=3)
    delivery_preferences: Optional[str] = None
    missing_information: List[str] = Field(default_factory=list, description="Missing essential parameters")
    clarification_questions: List[str] = Field(default_factory=list, description="Clarifying questions if ambiguous")
    is_ambiguous: bool = Field(False, description="True if prompt lacks critical parameters or requires user clarification")
    confidence_score: float = Field(1.0, ge=0.0, le=1.0)
    source: str = Field("gemini", description="Source of extraction: 'gemini' or 'simulated_fallback'")
    quarantine_flags: List[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v and v.upper() not in ["USD", "EUR", "GBP", "CAD", "AUD"]:
            return "USD"
        return v.upper() if v else "USD"

    model_config = ConfigDict(extra="ignore")

# ==============================================================================
# 2. Purchase Proposal Schemas
# ==============================================================================
class ProposalGenerationRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    user_id: Optional[str] = "usr-alex-01"
    module: Optional[ModuleScope] = ModuleScope.PERSONAL
    care_profile_id: Optional[str] = None
    untrusted_merchant_text: Optional[str] = Field(None, max_length=2000, description="External untrusted merchant/catalog text to quarantine")

class AIProposalItem(BaseModel):
    product_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=200)
    sku: Optional[str] = None
    category: Optional[str] = "General"
    quantity: int = Field(1, gt=0)
    unit_price_cents: Optional[int] = Field(None, ge=0, description="Unit price in minor currency units ONLY when verified")
    is_verified_price: bool = Field(False, description="False if price is estimated or unverified")

    model_config = ConfigDict(extra="ignore")

class AIPurchaseProposal(BaseModel):
    proposal_id: Optional[str] = None
    user_id: str
    care_profile_id: Optional[str] = None
    module: ModuleScope
    user_intent: str
    merchant_id: Optional[str] = None
    merchant_name: str
    items: List[AIProposalItem] = Field(..., min_length=1)
    subtotal_cents: Optional[int] = Field(None, ge=0)
    tax_cents: Optional[int] = Field(None, ge=0)
    shipping_cents: Optional[int] = Field(None, ge=0)
    total_cents: Optional[int] = Field(None, ge=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    is_transaction_ready: bool = Field(False, description="True only if merchant, catalog items, and pricing are verified")
    missing_information: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    ai_reasoning: str
    created_at: datetime = Field(default_factory=utc_now)
    source: str = Field("gemini", description="'gemini' or 'simulated_fallback'")
    quarantine_flags: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

# ==============================================================================
# 3. Business Affordability & Cashflow Schemas
# ==============================================================================
class CashflowExplanationRequest(BaseModel):
    user_id: Optional[str] = "usr-alex-01"
    query: Optional[str] = Field("Can we afford our upcoming obligations?", max_length=500)

class BusinessAffordabilityAnalysis(BaseModel):
    summary: str
    financial_observations: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    runway_days_projection: Optional[int] = None
    deterministic_figures: Dict[str, Any] = Field(
        default_factory=dict,
        description="Authoritative totals calculated deterministically in Python"
    )
    forecast_assumptions: List[str] = Field(default_factory=list)
    data_freshness_timestamp: datetime = Field(default_factory=utc_now)
    source: str = Field("gemini", description="'gemini' or 'simulated_fallback'")
    disclaimer: str = Field(
        "AI financial intelligence is informational and does not constitute authoritative legal or financial advice.",
        description="Statutory non-advice disclaimer"
    )

    model_config = ConfigDict(extra="ignore")

# ==============================================================================
# 4. CARE Assistance Schemas
# ==============================================================================
class CareAssistRequest(BaseModel):
    care_profile_id: Optional[str] = "care-eleanor-01"
    query: Optional[str] = Field(None, max_length=500)

class CareReorderAssistance(BaseModel):
    summary: str
    recipient_name: str
    identified_items: List[Dict[str, Any]] = Field(default_factory=list)
    reorder_schedule_status: str
    requires_human_guardian_review: bool = True
    missing_information: List[str] = Field(default_factory=list)
    guardian_notes: str
    is_emergency_bypass_requested: bool = False
    safety_advisory: str = Field(
        "CRITICAL: AI assistance never prescribes, alters dosage, or bypasses guardian consent.",
        description="Statutory safety advisory"
    )
    source: str = Field("gemini", description="'gemini' or 'simulated_fallback'")

    model_config = ConfigDict(extra="ignore")

# ==============================================================================
# 5. AI Service Health & Status
# ==============================================================================
class AIHealthStatus(BaseModel):
    status: str = Field(..., description="'connected', 'simulated_fallback', or 'unavailable'")
    provider: str = Field("google-genai", description="AI SDK provider")
    model: str
    has_api_key: bool
    latency_ms: Optional[float] = None
    features: List[str] = Field(
        default_factory=lambda: [
            "Intent Extraction",
            "Structured Proposals",
            "Prompt Injection Defense",
            "Business FLOW Cashflow Explanation",
            "CARE Assisted Reorders"
        ]
    )
    timestamp: datetime = Field(default_factory=utc_now)
