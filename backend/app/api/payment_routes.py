from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.config import settings
from app.models.entities import Transaction
from app.payments.paypal_client import PayPalClient
from app.payments.paypal_service import PayPalService
from app.payments.schemas import (
    PayPalStatusResponse,
    PayPalOrderCreateRequest,
    PayPalOrderResponse,
    PayPalCaptureRequest,
    PayPalCaptureResponse,
    PaymentDetailResponse,
)
from app.payments.exceptions import (
    PaymentError,
    PaymentVerificationError,
    PaymentPolicyDeniedError,
    PaymentCaptureError,
    PaymentAuthenticationError,
    PaymentConfigurationError,
    PaymentIdempotencyConflict,
)

router = APIRouter(prefix="/payments", tags=["PayPal Sandbox Gateway"])

paypal_client = PayPalClient()
payment_service = PayPalService(client=paypal_client)

@router.get("/status", response_model=PayPalStatusResponse)
async def get_payment_status():
    """
    Returns PayPal Gateway status and configuration mode with verified connectivity check.
    Guarantees strict Sandbox routing with transparent simulated indicator if unconfigured.
    """
    is_sim = paypal_client.is_simulated
    configured = bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_ID != "your_paypal_sandbox_client_id_here")
    conn_status = await paypal_client.check_connectivity()
    return PayPalStatusResponse(
        status=conn_status,
        mode=settings.PAYPAL_MODE,
        base_url=settings.PAYPAL_BASE_URL,
        is_simulated=is_sim,
        client_id_configured=configured,
    )

@router.post("/{proposal_id}/create-order", response_model=PayPalOrderResponse)
async def create_paypal_order(
    proposal_id: str,
    req: Optional[PayPalOrderCreateRequest] = None,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Gated PayPal order creation.
    Strictly checks that the proposal is in APPROVED state, validates cryptographic fingerprint,
    and advances lifecycle to PAYMENT_PENDING before placing order in PayPal Sandbox.
    """
    idempotency_key = req.idempotency_key if req else None
    current_user_id = None
    if authorization and authorization.startswith("Bearer "):
        try:
            from app.api.auth_routes import decode_access_token
            token = authorization.split(" ", 1)[1].strip()
            payload = decode_access_token(token)
            if payload and "sub" in payload:
                current_user_id = payload["sub"]
        except Exception:
            pass

    try:
        return await payment_service.create_order_for_proposal(
            db=db,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            current_user_id=current_user_id,
        )
    except PaymentIdempotencyConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except PaymentPolicyDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except PaymentVerificationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PaymentAuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except PaymentCaptureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PaymentConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected payment error: {str(e)}")

@router.post("/{proposal_id}/capture", response_model=PayPalCaptureResponse)
async def capture_paypal_order(
    proposal_id: str,
    req: PayPalCaptureRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authoritative server-side verification and capture of PayPal Sandbox order.
    Re-verifies transaction fingerprint and approval before capturing.
    Detects tampering and blocks execution.
    """
    if req.proposal_id != proposal_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Route proposal ID '{proposal_id}' does not match body proposal ID '{req.proposal_id}'.",
        )
    try:
        return await payment_service.capture_order_for_proposal(
            db=db,
            proposal_id=proposal_id,
            paypal_order_id=req.paypal_order_id,
            idempotency_key=req.idempotency_key,
        )
    except PaymentIdempotencyConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except PaymentPolicyDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except PaymentVerificationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PaymentAuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except PaymentCaptureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PaymentConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Capture failed: {str(e)}")

@router.post("/{proposal_id}/reconcile", response_model=PayPalCaptureResponse)
async def reconcile_paypal_order(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Explicit timeout and connectivity reconciliation for PayPal orders.
    Reconciles external provider order state against internal Trust Engine invariants.
    """
    try:
        return await payment_service.reconcile_timed_out_capture(
            db=db,
            proposal_id=proposal_id,
        )
    except PaymentIdempotencyConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except PaymentPolicyDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except PaymentVerificationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PaymentCaptureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Reconciliation error: {str(e)}")

@router.post("/{proposal_id}/recover-order", response_model=PayPalOrderResponse)
async def recover_paypal_order(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Explicit crash and timeout recovery for stranded order creation attempts.
    Reconciles provider state using persisted idempotency key and recovers without duplicate orders.
    """
    try:
        return await payment_service.recover_order_creation(
            db=db,
            proposal_id=proposal_id,
            stale_timeout_seconds=0,
        )
    except PaymentIdempotencyConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except PaymentPolicyDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except PaymentVerificationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PaymentCaptureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Order recovery error: {str(e)}")

@router.get("/transactions", response_model=List[PaymentDetailResponse])
async def list_transactions(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Retrieves authoritative payment attempt records ordered newest first."""
    query = (
        select(Transaction)
        .order_by(desc(Transaction.created_at))
        .offset(offset)
        .limit(limit)
    )
    res = await db.execute(query)
    txs = res.scalars().all()
    return [
        PaymentDetailResponse(
            transaction_id=tx.id,
            proposal_id=tx.proposal_id,
            user_id=tx.user_id,
            state=tx.state.value if hasattr(tx.state, "value") else str(tx.state),
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
        for tx in txs
    ]

@router.get("/{proposal_id}", response_model=PaymentDetailResponse)
async def get_payment_details(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieves payment attempt record and PayPal order metadata for a proposal."""
    try:
        return await payment_service.get_payment_details(db=db, proposal_id=proposal_id)
    except PaymentVerificationError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.post("/{proposal_id}/cancel")
async def cancel_payment(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Safely transitions pending payment to CANCELLED."""
    try:
        await payment_service.cancel_payment_attempt(db=db, proposal_id=proposal_id)
        return {"status": "cancelled", "proposal_id": proposal_id}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
