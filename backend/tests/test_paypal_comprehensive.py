import uuid
import time
from datetime import datetime, timezone, timedelta
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_database
from app.models.entities import (
    PurchaseProposal,
    ProposalItem,
    Transaction,
    TransactionFingerprint,
    Approval,
    ConsentPolicy,
)
from app.models.enums import (
    TransactionLifecycleState,
    AuditEventType,
    ApprovalMode,
)
from app.payments.paypal_client import (
    PayPalClient,
    cents_to_paypal_amount,
    paypal_amount_to_cents,
)
from app.payments.paypal_service import (
    PayPalService,
    to_str,
    recompute_proposal_fingerprint,
)
from app.payments.exceptions import (
    PaymentVerificationError,
    PaymentCaptureError,
    PaymentAuthenticationError,
    PaymentConfigurationError,
)

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_database()

async def create_approved_proposal(ac: AsyncClient, title="Test Item", unit_cents=2500):
    payload = {
        "user_id": "usr-alex-01",
        "module": "PERSONAL",
        "title": title,
        "merchant_id": "m-keychron-01",
        "merchant_name": "Keychron Official",
        "items": [
            {
                "product_id": f"prod-{uuid.uuid4().hex[:6]}",
                "name": title,
                "category": "Peripherals",
                "quantity": 1,
                "unit_price_cents": unit_cents,
            }
        ],
        "subtotal_cents": unit_cents,
        "tax_cents": int(unit_cents * 0.08),
        "shipping_cents": 0,
        "currency": "USD",
        "ai_reasoning": "Test automated proposal.",
    }
    res_p = await ac.post("/api/trust/proposals", json=payload)
    assert res_p.status_code == 200
    p = res_p.json()
    p_id = p["id"]
    fp = p["fingerprint_sha256"]

    await ac.post(
        f"/api/trust/transactions/{p_id}/transition",
        json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
    )
    await ac.post(
        f"/api/trust/transactions/{p_id}/transition",
        json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
    )
    await ac.post(
        "/api/trust/approve",
        json={
            "proposal_id": p_id,
            "approver_user_id": "usr-alex-01",
            "approval_type": "USER_DIRECT",
            "fingerprint_hash": fp,
            "ttl_seconds": 3600,
        },
    )
    return p_id, fp

def test_to_str_helper():
    assert to_str(None) == ""
    assert to_str("hello") == "hello"
    assert to_str(TransactionLifecycleState.APPROVED) == "APPROVED"

@pytest.mark.asyncio
async def test_paypal_client_caching_and_connectivity():
    client = PayPalClient(client_id="mock_id", client_secret="mock_secret")

    # Cached token check
    client._access_token = "cached_live_token"
    client._token_expires_at = time.time() + 3600
    assert await client.check_connectivity() == "connected"
    assert await client.get_access_token() == "cached_live_token"

    # Connectivity exception branch
    client._token_expires_at = 0
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Network down")
        assert await client.check_connectivity() == "unavailable"

@pytest.mark.asyncio
async def test_paypal_client_token_failure_branches():
    client = PayPalClient(client_id="mock_id", client_secret="mock_secret", max_retries=1)

    # 500 error after retries
    mock_500 = MagicMock(status_code=500, text="Internal Server Error")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, patch("time.sleep"):
        mock_post.return_value = mock_500
        with pytest.raises(PaymentAuthenticationError) as exc:
            await client.get_access_token()
        assert "500" in str(exc.value)

    # RequestError network failure
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, patch("time.sleep"):
        mock_post.side_effect = httpx.ReadTimeout("Read timed out")
        with pytest.raises(PaymentAuthenticationError) as exc:
            await client.get_access_token()
        assert "failure" in str(exc.value).lower()

@pytest.mark.asyncio
async def test_paypal_client_create_order_failure_branches():
    client = PayPalClient(client_id="mock_id", client_secret="mock_secret", max_retries=1)
    client._access_token = "valid_tok"
    client._token_expires_at = time.time() + 3600

    items = [{"name": "Item", "quantity": 1, "unit_price_cents": 1000, "total_cents": 1000}]

    # 401 error
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=401, text="Auth rejected")
        with pytest.raises(PaymentAuthenticationError):
            await client.create_order(
                total_cents=1000,
                currency="USD",
                items=items,
                custom_id="prop-1",
                idempotency_key="idemp-1",
            )

    # 422 client error
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=422, text="Unprocessable Entity")
        with pytest.raises(PaymentCaptureError):
            await client.create_order(
                total_cents=1000,
                currency="USD",
                items=items,
                custom_id="prop-1",
                idempotency_key="idemp-1",
            )

    # 500 server error
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, patch("time.sleep"):
        mock_post.return_value = MagicMock(status_code=500, text="Server Error")
        with pytest.raises(PaymentCaptureError):
            await client.create_order(
                total_cents=1000,
                currency="USD",
                items=items,
                custom_id="prop-1",
                idempotency_key="idemp-1",
            )

    # Network timeout
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, patch("time.sleep"):
        mock_post.side_effect = httpx.ConnectTimeout("Connect Timeout")
        with pytest.raises(PaymentCaptureError):
            await client.create_order(
                total_cents=1000,
                currency="USD",
                items=items,
                custom_id="prop-1",
                idempotency_key="idemp-1",
            )

@pytest.mark.asyncio
async def test_paypal_client_capture_order_failure_branches():
    client = PayPalClient(client_id="mock_id", client_secret="mock_secret", max_retries=1)
    client._access_token = "valid_tok"
    client._token_expires_at = time.time() + 3600

    # 401 capture error
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=401, text="Auth failed")
        with pytest.raises(PaymentAuthenticationError):
            await client.capture_order("ORD-1", idempotency_key="idemp-1")

    # 400 capture error
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        with pytest.raises(PaymentCaptureError):
            await client.capture_order("ORD-1", idempotency_key="idemp-1")

    # 500 capture error
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, patch("time.sleep"):
        mock_post.return_value = MagicMock(status_code=500, text="Server Error")
        with pytest.raises(PaymentCaptureError):
            await client.capture_order("ORD-1", idempotency_key="idemp-1")

    # Network timeout during capture
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, patch("time.sleep"):
        mock_post.side_effect = httpx.ReadTimeout("Timeout")
        with pytest.raises(PaymentCaptureError):
            await client.capture_order("ORD-1", idempotency_key="idemp-1")

    # get_order 500 error
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MagicMock(status_code=500, text="Get Order Server Error")
        with pytest.raises(PaymentCaptureError):
            await client.get_order("ORD-1")

@pytest.mark.asyncio
async def test_paypal_service_order_creation_guards():
    service = PayPalService()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Guard Test 1", unit_cents=1000)

    async with AsyncSessionLocal() as session:
        # Create order succeeds
        ord_resp = await service.create_order_for_proposal(session, p_id)
        assert ord_resp.order_id is not None

        # Idempotent re-call returns cached order
        ord_resp_2 = await service.create_order_for_proposal(session, p_id)
        assert ord_resp_2.order_id == ord_resp.order_id

@pytest.mark.asyncio
async def test_paypal_service_capture_guards_and_failures():
    service = PayPalService()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Capture Guard Prop", unit_cents=2000)
        ord_res = await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})
        paypal_order_id = ord_res.json()["order_id"]

    async with AsyncSessionLocal() as session:
        # Guard: Mismatched PayPal order ID
        with pytest.raises(PaymentVerificationError) as exc:
            await service.capture_order_for_proposal(session, p_id, "WRONG-ORDER-ID")
        assert "mismatch" in str(exc.value).lower()

        # Guard: Tamper detection (modify item price)
        from sqlalchemy import update
        await session.execute(
            update(ProposalItem).where(ProposalItem.proposal_id == p_id).values(unit_price_cents=99999)
        )
        await session.commit()

        with pytest.raises(PaymentVerificationError) as exc:
            await service.capture_order_for_proposal(session, p_id, paypal_order_id)
        assert "tamper" in str(exc.value).lower() or "fingerprint" in str(exc.value).lower()

@pytest.mark.asyncio
async def test_paypal_service_timeout_reconciliation_probe_error():
    mock_client = AsyncMock()
    mock_client.is_simulated = False
    mock_client.capture_order.side_effect = PaymentCaptureError("Timeout connecting to PayPal")
    mock_client.get_order.side_effect = Exception("PayPal probe connection refused")

    service = PayPalService(client=mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Probe Error Prop", unit_cents=1500)
        ord_res = await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})
        order_id = ord_res.json()["order_id"]

    async with AsyncSessionLocal() as session:
        with pytest.raises(PaymentCaptureError) as exc:
            await service.capture_order_for_proposal(session, p_id, order_id)
        assert "reconciliation" in str(exc.value).lower()

        details = await service.get_payment_details(session, p_id)
        assert details.reconciliation_status == "RECONCILED_REQUIRED" or details.reconciliation_status == "RECONCILIATION_REQUIRED"

@pytest.mark.asyncio
async def test_paypal_service_real_capture_declined():
    mock_client = AsyncMock()
    mock_client.is_simulated = False
    mock_client.capture_order.return_value = {
        "id": "ORD-DECLINED-1",
        "status": "DECLINED",
        "purchase_units": [],
    }

    service = PayPalService(client=mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Declined Prop", unit_cents=1000)
        ord_res = await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})
        order_id = ord_res.json()["order_id"]

    async with AsyncSessionLocal() as session:
        with pytest.raises(PaymentCaptureError) as exc:
            await service.capture_order_for_proposal(session, p_id, order_id)
        assert "completed" in str(exc.value).lower() or "declined" in str(exc.value).lower()

@pytest.mark.asyncio
async def test_paypal_service_cancel_payment_attempt_branches():
    service = PayPalService()

    # 1. Non-existent proposal
    async with AsyncSessionLocal() as session:
        with pytest.raises(PaymentVerificationError) as exc:
            await service.cancel_payment_attempt(session, "non-existent-prop-id")
        assert "not found" in str(exc.value).lower()

    # 2. Existing proposal in PAYMENT_PENDING
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Cancel Service Test", unit_cents=1800)
        await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})

    async with AsyncSessionLocal() as session:
        await service.cancel_payment_attempt(session, p_id)
        details = await service.get_payment_details(session, p_id)
        assert details.state == "CANCELLED"

@pytest.mark.asyncio
async def test_paypal_service_capture_additional_branches():
    service = PayPalService()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Additional Branches", unit_cents=2200)
        ord_res = await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})
        order_id = ord_res.json()["order_id"]

    async with AsyncSessionLocal() as session:
        # 1. Missing transaction record check
        from sqlalchemy import delete
        await session.execute(delete(Transaction).where(Transaction.proposal_id == p_id))
        await session.commit()

        with pytest.raises(PaymentVerificationError) as exc:
            await service.capture_order_for_proposal(session, p_id, order_id)
        assert "no transaction record found" in str(exc.value).lower()

@pytest.mark.asyncio
async def test_paypal_service_capture_idempotent_return_and_simulation():
    # Test simulated capture execution and idempotent return
    service = PayPalService(client=PayPalClient())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Sim Idemp Prop", unit_cents=1400)
        ord_res = await ac.post(f"/api/payments/{p_id}/create-order", json={"proposal_id": p_id})
        order_id = ord_res.json()["order_id"]

    async with AsyncSessionLocal() as session:
        # First simulated capture
        cap1 = await service.capture_order_for_proposal(session, p_id, order_id)
        assert cap1.status == "SIMULATED"
        assert cap1.is_simulated is True

        # Second idempotent capture return
        cap2 = await service.capture_order_for_proposal(session, p_id, order_id)
        assert cap2.capture_id == cap1.capture_id
        assert cap2.status == "SIMULATED"
        assert cap2.is_simulated is True

@pytest.mark.asyncio
async def test_paypal_service_missing_fingerprint_and_linked_policy():
    service = PayPalService()

    # 1. Create proposal via API, then remove its fingerprint in DB
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id, fp = await create_approved_proposal(ac, title="Missing FP Prop", unit_cents=1000)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import delete
        await session.execute(delete(TransactionFingerprint).where(TransactionFingerprint.proposal_id == p_id))
        await session.commit()

        # Fails: lacks registered fingerprint
        with pytest.raises(PaymentVerificationError) as exc:
            await service.create_order_for_proposal(session, p_id)
        assert "fingerprint" in str(exc.value).lower()

    # 2. Proposal with linked ConsentPolicy during create_order and capture_order
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_id2, fp2 = await create_approved_proposal(ac, title="Linked Policy Prop", unit_cents=1200)

    async with AsyncSessionLocal() as session:
        now_utc = datetime.now(timezone.utc)
        pol = ConsentPolicy(
            id=str(uuid.uuid4()),
            user_id="usr-alex-01",
            scope="PERSONAL",
            title="Linked Valid Policy",
            max_per_order_cents=50000,
            monthly_budget_cents=100000,
            valid_until=now_utc + timedelta(days=30),
            is_revoked=False,
        )
        session.add(pol)
        await session.flush()

        from sqlalchemy import update
        await session.execute(
            update(Approval).where(Approval.fingerprint_hash == fp2).values(consent_policy_id=pol.id)
        )
        await session.commit()

        # Order creation evaluates linked policy successfully
        ord_res = await service.create_order_for_proposal(session, p_id2)
        assert ord_res.order_id is not None

        # Capture evaluates linked policy successfully
        cap_res = await service.capture_order_for_proposal(session, p_id2, ord_res.order_id)
        assert cap_res.status in ("COMPLETED", "SIMULATED")
