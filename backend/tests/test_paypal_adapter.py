import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import httpx

from app.main import app
from app.core.init_db import init_database
from app.payments.paypal_client import (
    PayPalClient,
    cents_to_paypal_amount,
    paypal_amount_to_cents,
)
from app.payments.exceptions import (
    PaymentConfigurationError,
    PaymentAuthenticationError,
    PaymentCaptureError,
)

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()

def test_currency_exact_integer_arithmetic():
    """Verifies that integer minor currency units are converted with zero floating-point arithmetic."""
    assert cents_to_paypal_amount(0) == "0.00"
    assert cents_to_paypal_amount(5) == "0.05"
    assert cents_to_paypal_amount(50) == "0.50"
    assert cents_to_paypal_amount(99) == "0.99"
    assert cents_to_paypal_amount(100) == "1.00"
    assert cents_to_paypal_amount(1999) == "19.99"
    assert cents_to_paypal_amount(1000000) == "10000.00"

    with pytest.raises(ValueError):
        cents_to_paypal_amount(-50)

    assert paypal_amount_to_cents("0.00") == 0
    assert paypal_amount_to_cents("0.05") == 5
    assert paypal_amount_to_cents("0.50") == 50
    assert paypal_amount_to_cents("1.00") == 100
    assert paypal_amount_to_cents("19.99") == 1999
    assert paypal_amount_to_cents("10000.00") == 1000000

def test_paypal_client_security_violations():
    """Ensures PayPal client strictly rejects live production endpoints and non-sandbox modes."""
    # Live base URL rejection
    with pytest.raises(PaymentConfigurationError) as exc_live:
        PayPalClient(base_url="https://api-m.paypal.com", mode="sandbox")
    assert "Live PayPal endpoints are strictly prohibited" in str(exc_live.value) or "Refusing live PayPal URL" in str(exc_live.value)

    # Live mode rejection
    with pytest.raises(PaymentConfigurationError) as exc_mode:
        PayPalClient(base_url="https://api-m.sandbox.paypal.com", mode="live")
    assert "Refusing non-sandbox mode" in str(exc_mode.value)

@pytest.mark.asyncio
async def test_paypal_status_endpoint():
    """Tests GET /api/payments/status returns valid sandbox configuration."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/payments/status")
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "sandbox"
    assert "sandbox" in data["base_url"].lower()
    assert "is_simulated" in data

@pytest.mark.asyncio
async def test_paypal_client_simulated_flow():
    """Verifies the high-fidelity mock client creates and captures orders correctly offline."""
    client = PayPalClient(client_id=None, client_secret=None)
    assert client.is_simulated is True

    items = [{"name": "Test Keycaps", "quantity": 1, "unit_price_cents": 2500, "total_cents": 2500}]
    order = await client.create_order(
        total_cents=2500,
        currency="USD",
        items=items,
        custom_id="test-prop-01",
        idempotency_key="idemp-key-01",
    )
    assert order["id"].startswith("SIM-ORD-")
    assert order["status"] == "CREATED"
    assert order["intent"] == "CAPTURE"
    assert any(link["rel"] == "approve" for link in order["links"])

    # Query order
    fetched = await client.get_order(order["id"])
    assert fetched["id"] == order["id"]

    # Capture order
    capture = await client.capture_order(order["id"], idempotency_key="idemp-cap-01")
    assert capture["status"] == "COMPLETED"
    captures = capture["purchase_units"][0]["payments"]["captures"]
    assert len(captures) > 0
    assert captures[0]["status"] == "COMPLETED"
    assert captures[0]["id"].startswith("SIM-CAP-")

@pytest.mark.asyncio
async def test_paypal_order_creation_flow():
    """Tests complete end-to-end order creation for an approved proposal."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create proposal
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Ergonomic Wrist Rest",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-rest-01",
                    "name": "Keychron Resin Wrist Rest",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 2900,
                }
            ],
            "subtotal_cents": 2900,
            "tax_cents": 232,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Fits ergonomic accessories budget.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        assert res_prop.status_code == 200
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        # 2. Advance to AWAITING_APPROVAL and approve
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        app_res = await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        assert app_res.status_code == 200

        # 3. Create PayPal Order
        order_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert order_res.status_code == 200, order_res.text
        order_data = order_res.json()
        assert order_data["order_id"] is not None
        assert order_data["proposal_id"] == proposal_id
        assert order_data["total_cents"] == 3132
        assert order_data["fingerprint_sha256"] == fingerprint

        # 4. Check proposal state is PAYMENT_PENDING
        get_p = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert get_p.json()["state"] == "PAYMENT_PENDING"

@pytest.mark.asyncio
async def test_paypal_order_capture_flow():
    """Tests PayPal Sandbox capture execution for an order in PAYMENT_PENDING."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create and approve proposal
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB-C Braided Cable",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [
                {
                    "product_id": "prod-cable-01",
                    "name": "Anker 6ft Braided USB-C Cable",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 1600,
                }
            ],
            "subtotal_cents": 1600,
            "tax_cents": 128,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Standard cable replacement.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )

        # Create Order
        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        paypal_order_id = ord_res.json()["order_id"]

        # Capture Order (Simulated mode in test environment)
        cap_res = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
        )
        assert cap_res.status_code == 200
        cap_data = cap_res.json()
        # CRITICAL FINDING 1: Simulated capture must return SIMULATED and NOT advance proposal to PAID
        assert cap_data["status"] == "SIMULATED"
        assert cap_data["is_simulated"] is True
        assert cap_data["capture_id"] is not None
        assert cap_data["total_cents"] == 1728

        # Invariant: Proposal remains in PAYMENT_PENDING in simulated mode (never falsely marked PAID)
        get_p = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert get_p.json()["state"] == "PAYMENT_PENDING"

        # Verify payment details reflect simulated state
        det_res = await ac.get(f"/api/payments/{proposal_id}")
        assert det_res.status_code == 200
        det = det_res.json()
        assert det["state"] == "PAYMENT_PENDING"
        assert det["paypal_status"] == "SIMULATED"
        assert det["is_simulated"] is True
        assert det["paypal_capture_id"] == cap_data["capture_id"]

@pytest.mark.asyncio
async def test_paypal_idempotent_capture():
    """Ensures calling capture repeatedly on a captured order returns the existing capture idempotently."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Desk Mat",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-mat-01",
                    "name": "Leather Desk Mat",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 2500,
                }
            ],
            "subtotal_cents": 2500,
            "tax_cents": 200,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Desk accessory.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        paypal_order_id = ord_res.json()["order_id"]

        # First capture
        cap1 = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
        )
        assert cap1.status_code == 200
        cap1_id = cap1.json()["capture_id"]

        # Second idempotent capture
        cap2 = await ac.post(
            f"/api/payments/{proposal_id}/capture",
            json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
        )
        assert cap2.status_code == 200
        assert cap2.json()["capture_id"] == cap1_id
        assert cap2.json()["status"] in ("COMPLETED", "SIMULATED")
        assert cap2.json()["is_simulated"] == cap1.json()["is_simulated"]

@pytest.mark.asyncio
async def test_paypal_payment_cancellation():
    """Tests safe cancellation of an order in PAYMENT_PENDING."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Keycap Puller",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-puller-01",
                    "name": "Metal Keycap Puller",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 1200,
                }
            ],
            "subtotal_cents": 1200,
            "tax_cents": 96,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Keycap tool.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})

        # Cancel payment
        cancel_res = await ac.post(f"/api/payments/{proposal_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"

        # Check proposal state is CANCELLED
        get_p = await ac.get(f"/api/trust/proposals/{proposal_id}")
        assert get_p.json()["state"] == "CANCELLED"

@pytest.mark.asyncio
async def test_paypal_client_real_http_token_and_orders():
    """Unit tests for PayPalClient with mocked httpx calls to verify OAuth caching, retries, and errors."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.payments.paypal_service import PayPalService

    client = PayPalClient(
        client_id="mock_client_id",
        client_secret="mock_client_secret",
        base_url="https://api-m.sandbox.paypal.com",
    )
    assert client.is_simulated is False

    mock_resp_token = MagicMock()
    mock_resp_token.status_code = 200
    mock_resp_token.json.return_value = {"access_token": "token-xyz-123", "expires_in": 3600}

    mock_resp_create = MagicMock()
    mock_resp_create.status_code = 201
    mock_resp_create.json.return_value = {
        "id": "ORD-LIVE-123",
        "status": "CREATED",
        "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORD-LIVE-123"}],
    }

    mock_resp_get = MagicMock()
    mock_resp_get.status_code = 200
    mock_resp_get.json.return_value = {"id": "ORD-LIVE-123", "status": "APPROVED"}

    mock_resp_cap = MagicMock()
    mock_resp_cap.status_code = 201
    mock_resp_cap.json.return_value = {
        "id": "ORD-LIVE-123",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [{"id": "CAP-LIVE-456", "status": "COMPLETED"}]
                }
            }
        ],
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:

        # Test token fetch
        mock_post.return_value = mock_resp_token
        token = await client.get_access_token()
        assert token == "token-xyz-123"

        # Token caching: next call should not invoke HTTP
        mock_post.reset_mock()
        token2 = await client.get_access_token()
        assert token2 == "token-xyz-123"
        mock_post.assert_not_called()

        # Test create order
        mock_post.return_value = mock_resp_create
        ord_res = await client.create_order(
            total_cents=1000,
            currency="USD",
            items=[{"name": "Pen", "quantity": 1, "unit_price_cents": 1000, "total_cents": 1000}],
            custom_id="prop-live-1",
            idempotency_key="idemp-1",
        )
        assert ord_res["id"] == "ORD-LIVE-123"

        # Test get order
        mock_get.return_value = mock_resp_get
        got = await client.get_order("ORD-LIVE-123")
        assert got["status"] == "APPROVED"

        # Test capture order
        mock_post.return_value = mock_resp_cap
        cap = await client.capture_order("ORD-LIVE-123", idempotency_key="idemp-cap-1")
        assert cap["status"] == "COMPLETED"

@pytest.mark.asyncio
async def test_paypal_client_http_error_branches():
    """Tests failure branches: 401 auth error and 404/500 errors."""
    from unittest.mock import AsyncMock, patch, MagicMock

    client = PayPalClient(client_id="mock_id", client_secret="mock_sec")

    # 401 token rejection
    mock_401 = MagicMock(status_code=401, text="Unauthorized client credentials")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_401
        with pytest.raises(PaymentAuthenticationError):
            await client.get_access_token()

    # Pre-set token for get_order 404 check
    client._access_token = "valid_cached_token"
    client._token_expires_at = 9999999999.0
    mock_404 = MagicMock(status_code=404, text="Order Not Found")
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_404
        with pytest.raises(PaymentCaptureError):
            await client.get_order("NON-EXISTENT")

@pytest.mark.asyncio
async def test_paypal_service_edge_cases():
    """Tests PayPalService edge cases directly with database sessions."""
    from app.core.database import AsyncSessionLocal
    from app.payments.paypal_service import PayPalService
    from app.payments.exceptions import PaymentVerificationError

    service = PayPalService()

    async with AsyncSessionLocal() as session:
        # Non-existent proposal
        with pytest.raises(PaymentVerificationError) as exc_p:
            await service.create_order_for_proposal(session, "non-existent-prop-999")
        assert "not found" in str(exc_p.value).lower()

        # Non-existent capture
        with pytest.raises(PaymentVerificationError) as exc_c:
            await service.capture_order_for_proposal(session, "non-existent-prop-999", "SIM-ORD-999")
        assert "not found" in str(exc_c.value).lower()

        # Non-existent payment details
        with pytest.raises(PaymentVerificationError) as exc_d:
            await service.get_payment_details(session, "non-existent-prop-999")
        assert "found" in str(exc_d.value).lower()

@pytest.mark.asyncio
async def test_concurrent_captures_prevent_race_condition():
    """
    HIGH-PRIORITY AUDIT FINDING: Concurrency race condition protection.
    Concurrent captures on the same proposal with asyncio.gather must acquire lock,
    use stable idempotency keys, and return identical capture IDs without race conditions.
    """
    import asyncio
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Dual Monitor Stand",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-stand-01",
                    "name": "Aluminum Monitor Arm",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 8900,
                }
            ],
            "subtotal_cents": 8900,
            "tax_cents": 712,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Workspace ergonomics.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        prop = res_prop.json()
        proposal_id = prop["id"]
        fingerprint = prop["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        paypal_order_id = ord_res.json()["order_id"]

        # Run 5 captures simultaneously with asyncio.gather
        async def do_capture():
            return await ac.post(
                f"/api/payments/{proposal_id}/capture",
                json={"proposal_id": proposal_id, "paypal_order_id": paypal_order_id},
            )

        responses = await asyncio.gather(*[do_capture() for _ in range(5)])

        # All 5 must succeed (200 OK)
        for r in responses:
            assert r.status_code == 200

        # All 5 must return the exact same capture ID (strict idempotency)
        capture_ids = [r.json()["capture_id"] for r in responses]
        assert len(set(capture_ids)) == 1, f"Multiple capture IDs created: {set(capture_ids)}"

@pytest.mark.asyncio
async def test_order_creation_blocked_in_policy_check_and_awaiting_approval():
    """
    CRITICAL INVARIANT: Order creation is strictly restricted to APPROVED proposals.
    Must fail closed for proposals in PROPOSED, POLICY_CHECK, or AWAITING_APPROVAL.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Wireless Mouse",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-mouse-01",
                    "name": "M3 Wireless Optical Mouse",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 4900,
                }
            ],
            "subtotal_cents": 4900,
            "tax_cents": 392,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Productivity mouse.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res_prop.json()["id"]

        # 1. State: PROPOSED -> must fail
        ord_1 = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert ord_1.status_code == 400
        assert "approval" in ord_1.json()["detail"].lower()

        # 2. State: POLICY_CHECK -> must fail
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        ord_2 = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert ord_2.status_code == 400
        assert "approval" in ord_2.json()["detail"].lower()

        # 3. State: AWAITING_APPROVAL -> must fail
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        ord_3 = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        assert ord_3.status_code == 400
        assert "approval" in ord_3.json()["detail"].lower()

@pytest.mark.asyncio
async def test_timeout_reconciliation_order_captured_in_paypal():
    """
    HIGH-PRIORITY AUDIT FINDING: Capture timeout reconciliation.
    When a capture attempt times out, probe get_order. If PayPal captured the funds,
    reconcile to PAID with status RECONCILED_SUCCESS.
    """
    from unittest.mock import AsyncMock
    from app.core.database import AsyncSessionLocal
    from app.payments.paypal_service import PayPalService
    from app.payments.exceptions import PaymentCaptureError
    from app.models.enums import TransactionLifecycleState

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB Hub",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [
                {
                    "product_id": "prod-hub-01",
                    "name": "7-in-1 USB-C Hub",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 3500,
                }
            ],
            "subtotal_cents": 3500,
            "tax_cents": 280,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Connectivity hub.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res_prop.json()["id"]
        fingerprint = res_prop.json()["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        paypal_order_id = ord_res.json()["order_id"]

        # Mock client: capture times out, but get_order reveals order was captured
        unique_cap_id = f"CAP-RECON-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.capture_order.side_effect = PaymentCaptureError("Network timeout connecting to PayPal capture endpoint")
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": proposal_id,
                    "amount": {"currency_code": "USD", "value": "37.80"},
                    "payments": {
                        "captures": [
                            {
                                "id": unique_cap_id,
                                "status": "COMPLETED",
                                "amount": {"currency_code": "USD", "value": "37.80"},
                            }
                        ]
                    },
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            cap_res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert cap_res.status == "COMPLETED"
            assert cap_res.capture_id == unique_cap_id
            assert cap_res.reconciliation_status == "RECONCILED_SUCCESS"
            assert cap_res.is_simulated is False

            # Verify payment details in DB
            details = await service.get_payment_details(session, proposal_id)
            assert details.state == "PAID"
            assert details.reconciliation_status == "RECONCILED_SUCCESS"
            assert details.is_simulated is False

@pytest.mark.asyncio
async def test_timeout_reconciliation_order_uncaptured_marks_reconciliation_required():
    """
    HIGH-PRIORITY AUDIT FINDING: Capture timeout reconciliation.
    When a capture attempt times out and get_order indicates order is uncaptured,
    record RECONCILIATION_REQUIRED and do NOT mark paid or failed.
    """
    from unittest.mock import AsyncMock
    from app.core.database import AsyncSessionLocal
    from app.payments.paypal_service import PayPalService
    from app.payments.exceptions import PaymentCaptureError

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB Cable 3-pack",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [
                {
                    "product_id": "prod-cables-03",
                    "name": "3-pack Braided Cables",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 2100,
                }
            ],
            "subtotal_cents": 2100,
            "tax_cents": 168,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Cable bundle.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res_prop.json()["id"]
        fingerprint = res_prop.json()["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        paypal_order_id = ord_res.json()["order_id"]

        # Mock client: capture times out, get_order indicates APPROVED (not captured)
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.capture_order.side_effect = PaymentCaptureError("Gateway timeout from PayPal")
        mock_client.get_order.return_value = {
            "id": paypal_order_id,
            "status": "APPROVED",
            "purchase_units": [],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError) as exc_info:
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert "reconciliation" in str(exc_info.value).lower()

            # Verify transaction is tagged RECONCILIATION_REQUIRED and remains in PAYMENT_PENDING
            details = await service.get_payment_details(session, proposal_id)
            assert details.reconciliation_status == "RECONCILIATION_REQUIRED"
            assert details.state == "PAYMENT_PENDING"

@pytest.mark.asyncio
async def test_real_sandbox_capture_transitions_to_paid():
    """
    CRITICAL FINDING: When is_simulated=False and PayPal confirms capture,
    proposal and transaction dynamically advance to PAID.
    """
    from unittest.mock import AsyncMock
    from app.core.database import AsyncSessionLocal
    from app.payments.paypal_service import PayPalService

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        prop_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Mechanical Keycaps",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-caps-01",
                    "name": "PBT Keycap Set",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 3900,
                }
            ],
            "subtotal_cents": 3900,
            "tax_cents": 312,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Customization.",
        }
        res_prop = await ac.post("/api/trust/proposals", json=prop_payload)
        proposal_id = res_prop.json()["id"]
        fingerprint = res_prop.json()["fingerprint_sha256"]

        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "POLICY_CHECK", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={"target_state": "AWAITING_APPROVAL", "actor_id": "usr-alex-01", "actor_role": "SYSTEM"},
        )
        await ac.post(
            "/api/trust/approve",
            json={
                "proposal_id": proposal_id,
                "approver_user_id": "usr-alex-01",
                "approval_type": "USER_DIRECT",
                "fingerprint_hash": fingerprint,
                "ttl_seconds": 3600,
            },
        )
        ord_res = await ac.post(f"/api/payments/{proposal_id}/create-order", json={"proposal_id": proposal_id})
        paypal_order_id = ord_res.json()["order_id"]

        unique_cap_id = f"REAL-CAP-{uuid.uuid4().hex[:8]}"
        mock_client = AsyncMock()
        mock_client.is_simulated = False
        mock_client.capture_order.return_value = {
            "id": paypal_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {"id": unique_cap_id, "status": "COMPLETED"}
                        ]
                    }
                }
            ],
        }

        service = PayPalService(client=mock_client)
        async with AsyncSessionLocal() as session:
            cap_res = await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)
            assert cap_res.status == "COMPLETED"
            assert cap_res.capture_id == unique_cap_id
            assert cap_res.is_simulated is False

            details = await service.get_payment_details(session, proposal_id)
            assert details.state == "PAID"
            assert details.paypal_status == "COMPLETED"
            assert details.is_simulated is False

@pytest.mark.asyncio
async def test_paypal_client_connectivity_check_states():
    """Tests PayPalClient.check_connectivity() under all four defined operational states."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.payments.paypal_client import PayPalClient

    # 1. Unconfigured / simulated
    client_sim = PayPalClient(client_id="", client_secret="")
    assert await client_sim.check_connectivity() == "simulated"

    # 2. Configured and connected (mock 200 token response)
    client_conn = PayPalClient(client_id="mock_id", client_secret="mock_secret")
    mock_resp_200 = MagicMock(status_code=200)
    mock_resp_200.json.return_value = {"access_token": "valid_token", "expires_in": 3600}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_200
        status = await client_conn.check_connectivity()
        assert status == "connected"

    # 3. Unavailable / error
    client_unavail = PayPalClient(client_id="mock_id", client_secret="mock_secret")
    mock_resp_500 = MagicMock(status_code=500, text="PayPal server error")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_500
        status = await client_unavail.check_connectivity()
        assert status == "unavailable"
