import uuid
from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_database
from app.payments.exceptions import (
    PaymentVerificationError,
    PaymentAuthenticationError,
    PaymentCaptureError,
    PaymentConfigurationError,
    PaymentIdempotencyConflict,
)
from app.payments.paypal_service import PayPalService
from app.models.entities import PurchaseProposal, Transaction, TransactionFingerprint, Approval
from app.models.enums import TransactionLifecycleState

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()

# ==============================================================================
# ROUTE EXCEPTION HANDLERS COVERAGE
# ==============================================================================

@pytest.mark.asyncio
async def test_create_order_route_exceptions():
    """Tests all exception branches of POST /api/payments/{proposal_id}/create-order."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        pid = f"prop-{uuid.uuid4().hex[:8]}"

        # 1. PaymentVerificationError -> 400
        res = await ac.post(f"/api/payments/{pid}/create-order")
        assert res.status_code == 400

        # 2. PaymentAuthenticationError -> 502
        with patch("app.api.payment_routes.payment_service.create_order_for_proposal", side_effect=PaymentAuthenticationError("Auth failed")):
            res = await ac.post(f"/api/payments/{pid}/create-order")
            assert res.status_code == 502

        # 3. PaymentCaptureError -> 400
        with patch("app.api.payment_routes.payment_service.create_order_for_proposal", side_effect=PaymentCaptureError("Order rejected")):
            res = await ac.post(f"/api/payments/{pid}/create-order")
            assert res.status_code == 400

        # 4. PaymentConfigurationError -> 500
        with patch("app.api.payment_routes.payment_service.create_order_for_proposal", side_effect=PaymentConfigurationError("Config error")):
            res = await ac.post(f"/api/payments/{pid}/create-order")
            assert res.status_code == 500

        # 5. Generic Exception -> 500
        with patch("app.api.payment_routes.payment_service.create_order_for_proposal", side_effect=RuntimeError("System crash")):
            res = await ac.post(f"/api/payments/{pid}/create-order")
            assert res.status_code == 500

@pytest.mark.asyncio
async def test_capture_order_route_exceptions():
    """Tests all exception branches of POST /api/payments/{proposal_id}/capture."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        pid = f"prop-{uuid.uuid4().hex[:8]}"
        mismatched_pid = f"prop-{uuid.uuid4().hex[:8]}"

        # 1. Route proposal ID mismatch with body proposal ID -> 400
        res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": mismatched_pid, "paypal_order_id": "ORD-1"})
        assert res.status_code == 400
        assert "does not match" in res.json()["detail"]

        # 2. PaymentIdempotencyConflict -> 409
        with patch("app.api.payment_routes.payment_service.capture_order_for_proposal", side_effect=PaymentIdempotencyConflict("In progress")):
            res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": pid, "paypal_order_id": "ORD-1"})
            assert res.status_code == 409

        # 3. PaymentVerificationError -> 400
        with patch("app.api.payment_routes.payment_service.capture_order_for_proposal", side_effect=PaymentVerificationError("Fingerprint mismatch")):
            res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": pid, "paypal_order_id": "ORD-1"})
            assert res.status_code == 400

        # 4. PaymentAuthenticationError -> 502
        with patch("app.api.payment_routes.payment_service.capture_order_for_proposal", side_effect=PaymentAuthenticationError("Auth token expired")):
            res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": pid, "paypal_order_id": "ORD-1"})
            assert res.status_code == 502

        # 5. PaymentCaptureError -> 400
        with patch("app.api.payment_routes.payment_service.capture_order_for_proposal", side_effect=PaymentCaptureError("Declined")):
            res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": pid, "paypal_order_id": "ORD-1"})
            assert res.status_code == 400

        # 6. PaymentConfigurationError -> 500
        with patch("app.api.payment_routes.payment_service.capture_order_for_proposal", side_effect=PaymentConfigurationError("Live endpoint detected")):
            res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": pid, "paypal_order_id": "ORD-1"})
            assert res.status_code == 500

        # 7. Generic Exception -> 500
        with patch("app.api.payment_routes.payment_service.capture_order_for_proposal", side_effect=ZeroDivisionError("Math error")):
            res = await ac.post(f"/api/payments/{pid}/capture", json={"proposal_id": pid, "paypal_order_id": "ORD-1"})
            assert res.status_code == 500

@pytest.mark.asyncio
async def test_reconcile_order_route_exceptions():
    """Tests all exception branches of POST /api/payments/{proposal_id}/reconcile."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        pid = f"prop-{uuid.uuid4().hex[:8]}"

        # 1. PaymentVerificationError -> 400
        with patch("app.api.payment_routes.payment_service.reconcile_timed_out_capture", side_effect=PaymentVerificationError("No tx")):
            res = await ac.post(f"/api/payments/{pid}/reconcile")
            assert res.status_code == 400

        # 2. PaymentCaptureError -> 400
        with patch("app.api.payment_routes.payment_service.reconcile_timed_out_capture", side_effect=PaymentCaptureError("Probe failed")):
            res = await ac.post(f"/api/payments/{pid}/reconcile")
            assert res.status_code == 400

        # 3. Generic Exception -> 500
        with patch("app.api.payment_routes.payment_service.reconcile_timed_out_capture", side_effect=RuntimeError("Reconciliation crash")):
            res = await ac.post(f"/api/payments/{pid}/reconcile")
            assert res.status_code == 500

@pytest.mark.asyncio
async def test_get_payment_details_and_cancel_route():
    """Tests GET /api/payments/{proposal_id} and POST /api/payments/{proposal_id}/cancel."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        pid = f"prop-{uuid.uuid4().hex[:8]}"

        # 1. Details non-existent -> 404
        res = await ac.get(f"/api/payments/{pid}")
        assert res.status_code == 404

        # 2. Cancel non-existent -> 400
        res = await ac.post(f"/api/payments/{pid}/cancel")
        assert res.status_code == 400

# ==============================================================================
# PAYPAL SERVICE EDGE CASES COVERAGE
# ==============================================================================

@pytest.mark.asyncio
async def test_paypal_service_reconcile_edge_cases():
    """Tests missing proposal, missing tx, missing order ID, empty probe in reconcile_timed_out_capture."""
    service = PayPalService()
    async with AsyncSessionLocal() as session:
        # 1. Non-existent proposal
        with pytest.raises(PaymentVerificationError, match="not found"):
            await service.reconcile_timed_out_capture(session, "non-existent-pid")

        # 2. Proposal exists but no transaction
        p = PurchaseProposal(
            id=f"prop-notx-{uuid.uuid4().hex[:8]}",
            user_id="usr-alex-01",
            module="PERSONAL",
            title="Item",
            merchant_id="m1",
            merchant_name="M1",
            subtotal_cents=1000,
            tax_cents=100,
            shipping_cents=0,
            total_cents=1100,
            currency="USD",
            ai_reasoning="Reason",
            state=TransactionLifecycleState.PAYMENT_PENDING,
        )
        session.add(p)
        await session.commit()

        with pytest.raises(PaymentVerificationError, match="No transaction record found"):
            await service.reconcile_timed_out_capture(session, p.id)

        # 3. Proposal with tx but no order ID
        tx = Transaction(
            id=f"tx-noid-{uuid.uuid4().hex[:8]}",
            proposal_id=p.id,
            user_id="usr-alex-01",
            state=TransactionLifecycleState.PAYMENT_PENDING,
            total_cents=1100,
            currency="USD",
            idempotency_key=f"idemp-{uuid.uuid4().hex[:8]}",
            fingerprint_sha256="fakehash",
            is_simulated=False,
        )
        session.add(tx)
        await session.commit()

        with pytest.raises(PaymentVerificationError, match="has no associated PayPal order ID"):
            await service.reconcile_timed_out_capture(session, p.id)

        # 4. Target order mismatch
        unique_order_id = f"ORD-STORED-{uuid.uuid4().hex[:8]}"
        tx.paypal_order_id = unique_order_id
        await session.commit()
        with pytest.raises(PaymentVerificationError, match="Order ID mismatch"):
            await service.reconcile_timed_out_capture(session, p.id, paypal_order_id="ORD-DIFFERENT")

        # 5. Empty or non-dict probe response
        mock_client = AsyncMock()
        mock_client.get_order.return_value = None
        service_mock = PayPalService(client=mock_client)
        with pytest.raises(PaymentCaptureError, match="Empty or malformed provider response"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

        # 6. Malformed float amount value
        mock_client.get_order.return_value = {
            "id": unique_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {"id": f"CAP-{uuid.uuid4().hex[:6]}", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "INVALID_FLOAT"}}
                        ]
                    }
                }
            ],
        }
        with pytest.raises(PaymentCaptureError, match="Malformed capture amount value"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

        # 7. No captures in payments list
        mock_client.get_order.return_value = {
            "id": unique_order_id,
            "status": "APPROVED",
            "purchase_units": [{"payments": {"captures": []}}],
        }
        with pytest.raises(PaymentCaptureError, match="No capture record found"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

        # 8. Malformed capture data: missing capture ID
        mock_client.get_order.return_value = {
            "id": unique_order_id,
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": "", "status": "COMPLETED"}]}}],
        }
        with pytest.raises(PaymentCaptureError, match="missing capture ID"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

        # 9. Provider capture status != COMPLETED (e.g. DENIED)
        mock_client.get_order.return_value = {
            "id": unique_order_id,
            "status": "DENIED",
            "purchase_units": [{"payments": {"captures": [{"id": f"CAP-{uuid.uuid4().hex[:6]}", "status": "DENIED"}]}}],
        }
        with pytest.raises(PaymentCaptureError, match="Provider capture status is 'DENIED'"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

        # 10. Missing amount object entirely
        mock_client.get_order.return_value = {
            "id": unique_order_id,
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": f"CAP-{uuid.uuid4().hex[:6]}", "status": "COMPLETED"}]}}],
        }
        with pytest.raises(PaymentCaptureError, match="Missing amount"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

        # 11. Missing currency or amount value
        mock_client.get_order.return_value = {
            "id": unique_order_id,
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": f"CAP-{uuid.uuid4().hex[:6]}", "status": "COMPLETED", "amount": {"currency_code": "USD"}}]}}],
        }
        with pytest.raises(PaymentCaptureError, match="Missing currency or amount value"):
            await service_mock.reconcile_timed_out_capture(session, p.id, paypal_order_id=unique_order_id)

@pytest.mark.asyncio
async def test_capture_order_non_timeout_failure():
    """Tests capture_order_for_proposal when provider raises non-timeout error (e.g. 422 Unprocessable)."""
    mock_client = AsyncMock()
    mock_client.is_simulated = False
    mock_client.capture_order.side_effect = PaymentCaptureError("422 Unprocessable Entity - Card Declined")

    service = PayPalService(client=mock_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        from tests.test_phase4_security_hardening import create_approved_proposal_and_order
        proposal_id, paypal_order_id, _ = await create_approved_proposal_and_order(ac)

        async with AsyncSessionLocal() as session:
            with pytest.raises(PaymentCaptureError, match="Card Declined"):
                await service.capture_order_for_proposal(session, proposal_id, paypal_order_id)

            # Proves transaction is transitioned to FAILED status
            details = await service.get_payment_details(session, proposal_id)
            assert details.paypal_status == "FAILED"
