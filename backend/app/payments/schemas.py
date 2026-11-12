from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class PayPalStatusResponse(BaseModel):
    status: str = Field(description="Operational status: 'unconfigured', 'configured', 'connected', 'unavailable', or 'simulated'")
    mode: str = Field(default="sandbox", description="PayPal mode - strictly 'sandbox'")
    base_url: str = Field(description="PayPal base URL endpoint")
    is_simulated: bool = Field(description="True if credentials are not configured and mock sandbox is used")
    client_id_configured: bool = Field(description="True if client ID is set")
    timestamp: datetime = Field(default_factory=utc_now)

class PayPalOrderCreateRequest(BaseModel):
    proposal_id: str = Field(description="ID of approved purchase proposal")
    idempotency_key: Optional[str] = Field(default=None, description="Optional caller idempotency key")

class PayPalOrderResponse(BaseModel):
    order_id: str = Field(description="PayPal v2 Order ID")
    status: str = Field(description="PayPal order status: CREATED, SAVED, APPROVED, VOIDED, COMPLETED")
    intent: str = Field(default="CAPTURE", description="Payment intent")
    approval_url: Optional[str] = Field(default=None, description="PayPal checkout approval URL")
    total_cents: int = Field(description="Total order amount in minor integer units (cents)")
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    proposal_id: str = Field(description="Linked MERIDIAN proposal ID")
    fingerprint_sha256: str = Field(description="Verified cryptographic SHA-256 fingerprint")
    is_simulated: bool = Field(default=False, description="True if simulated in offline mock mode")
    created_at: datetime = Field(default_factory=utc_now)

class PayPalCaptureRequest(BaseModel):
    proposal_id: str = Field(description="ID of approved proposal")
    paypal_order_id: str = Field(description="PayPal v2 Order ID to capture")
    idempotency_key: Optional[str] = Field(default=None, description="Optional caller idempotency key")

class PayPalCaptureResponse(BaseModel):
    capture_id: str = Field(description="PayPal v2 Capture ID")
    order_id: str = Field(description="PayPal v2 Order ID")
    status: str = Field(description="PayPal capture status: COMPLETED, PENDING, DECLINED, FAILED")
    total_cents: int = Field(description="Captured amount in integer cents")
    currency: str = Field(default="USD", description="Captured currency")
    proposal_id: str = Field(description="Linked MERIDIAN proposal ID")
    fingerprint_sha256: str = Field(description="Verified cryptographic SHA-256 fingerprint")
    is_simulated: bool = Field(default=False, description="True if simulated in offline mock mode")
    reconciliation_status: Optional[str] = Field(default=None, description="Optional reconciliation status")
    captured_at: datetime = Field(default_factory=utc_now)

class PaymentDetailResponse(BaseModel):
    transaction_id: str
    proposal_id: str
    user_id: str
    state: str
    total_cents: int
    currency: str
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    paypal_status: Optional[str] = None
    paypal_approval_url: Optional[str] = None
    fingerprint_sha256: str
    is_simulated: bool = False
    reconciliation_status: Optional[str] = None
    capture_idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime
