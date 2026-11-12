import pytest
import pytest_asyncio
import json
from unittest.mock import MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.ai.gemini_service import ai_service, GeminiService
from app.models.enums import ModuleScope, TransactionLifecycleState
from app.core.init_db import init_database

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_database()

# ==============================================================================
# 1. AI Health and Fallback Status
# ==============================================================================
@pytest.mark.asyncio
async def test_ai_status_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/ai/status")
        assert res.status_code == 200
        data = res.json()
        assert data["provider"].startswith("google-genai")
        assert data["status"] in ["connected", "simulated_fallback"]
        assert "features" in data
        assert len(data["features"]) >= 3

# ==============================================================================
# 2. Structured Intent Extraction Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_intent_extraction_valid_shopping_request():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "query": "I need a quiet wireless mechanical keyboard under $100 for programming.",
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
        }
        res = await ac.post("/api/ai/interpret", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["intent_type"] == "PURCHASE_INTENT"
        assert data["module"] == "PERSONAL"
        assert len(data["requested_products"]) > 0
        assert "Peripherals" in data["product_categories"]
        # $100 budget -> 10000 cents
        assert data["max_budget_cents"] == 10000
        assert data["currency"] == "USD"
        assert data["is_ambiguous"] is False

@pytest.mark.asyncio
async def test_intent_extraction_missing_budget_is_absent():
    """
    Core Invariant: Gemini must NOT invent missing financial numbers.
    If no budget is specified, max_budget_cents must remain null.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "query": "Find me good wireless earbuds with active noise cancellation.",
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
        }
        res = await ac.post("/api/ai/interpret", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["max_budget_cents"] is None
        assert any("budget" in item.lower() for item in data["missing_information"] + data["clarification_questions"])

@pytest.mark.asyncio
async def test_intent_extraction_ambiguous_request():
    """
    Ambiguous requests must ask clarification questions rather than drafting misleading proposals.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "query": "I want to buy something nice",
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
        }
        res = await ac.post("/api/ai/interpret", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["is_ambiguous"] is True
        assert len(data["clarification_questions"]) > 0

# ==============================================================================
# 3. Structured Proposal Generation Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_generate_proposal_matched_catalog_ready():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "query": "Keychron K3 Pro mechanical keyboard for coding",
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
        }
        res = await ac.post("/api/ai/propose", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["merchant_name"] == "Keychron Official"
        assert data["is_transaction_ready"] is True
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert item["unit_price_cents"] == 8400 # Exact catalog price
        assert item["is_verified_price"] is True
        assert data["total_cents"] == 9072 # $84.00 + 8% tax

@pytest.mark.asyncio
async def test_generate_proposal_unverified_unready():
    """
    When item is not in verified catalog, prices are unverified and proposal is not transaction-ready.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "query": "Custom handmade ceramic coffee mug from Etsy",
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
        }
        res = await ac.post("/api/ai/propose", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["is_transaction_ready"] is False
        assert data["items"][0]["unit_price_cents"] is None
        assert data["items"][0]["is_verified_price"] is False

# ==============================================================================
# 4. Business / FLOW Cashflow Intelligence Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_business_cashflow_explanation_deterministic_arithmetic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/ai/explain-cashflow", json={
            "user_id": "usr-alex-01",
            "query": "Can we afford to buy $2,500 of warehouse supplies this week?",
        })
        assert res.status_code == 200
        data = res.json()
        figs = data["deterministic_figures"]

        # Verify deterministic arithmetic: net = starting + receivables - payables
        starting = figs["starting_balance_cents"]
        payables = figs["total_upcoming_payables_cents"]
        receivables = figs["total_expected_receivables_cents"]
        expected_net = starting + receivables - payables
        assert figs["projected_net_cents"] == expected_net

        # Verify runway days is calculated
        assert figs["estimated_runway_days"] >= 0
        assert len(data["financial_observations"]) > 0
        assert len(data["disclaimer"]) > 0

# ==============================================================================
# 5. CARE Assisted Commerce Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_care_assist_reorder_and_safety_invariants():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/ai/care-assist", json={
            "care_profile_id": "care-eleanor-01",
            "query": "Check upcoming refill for glucose monitor sensors",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["recipient_name"] == "Eleanor Vance"
        assert len(data["identified_items"]) > 0
        # Invariant: Human guardian review cannot be bypassed
        assert data["requires_human_guardian_review"] is True
        assert "AI assistance never prescribes" in data["safety_advisory"]

# ==============================================================================
# 6. Separation of Powers Invariant
# ==============================================================================
@pytest.mark.asyncio
async def test_ai_cannot_authorize_payment():
    """
    Proves that calling an AI proposal endpoint creates an unapproved proposal schema,
    which cannot transition to PAID without human approval and trust engine verification.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Step 1: AI generates proposal
        prop_res = await ac.post("/api/ai/propose", json={
            "query": "Keychron K3 Pro",
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
        })
        ai_proposal = prop_res.json()

        # An AI proposal output has no state attribute of PAID or APPROVED
        assert "state" not in ai_proposal or ai_proposal.get("state") != "PAID"

        # Step 2: Attempting to directly trigger payment without Trust Engine approval must fail
        # Even if a malicious actor submitted a proposal to /trust/proposals, its state starts as PROPOSED
        trust_create = await ac.post("/api/trust/proposals", json={
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "AI Drafted Item",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [{
                "product_id": "prod-k3pro-01",
                "name": "Keychron K3 Pro",
                "quantity": 1,
                "unit_price_cents": 8400,
            }],
            "subtotal_cents": 8400,
            "tax_cents": 672,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "AI proposal test.",
        })
        assert trust_create.status_code == 200
        p_id = trust_create.json()["id"]
        assert trust_create.json()["state"] == "PROPOSED"

        # Attempt illegal skip transition PROPOSED -> PAID directly
        illegal_transition = await ac.post(f"/api/trust/transactions/{p_id}/transition", json={
            "target_state": "PAID",
            "actor_id": "ai-agent",
            "actor_role": "AI",
        })
        assert illegal_transition.status_code in (400, 403)
        detail_msg = illegal_transition.json()["detail"].lower()
        assert "forbidden" in detail_msg or "illegal" in detail_msg or "cannot transition" in detail_msg

# ==============================================================================
# 7. Mocked Live Gemini Client Execution & Error Resilience Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_mocked_gemini_client_intent_extraction():
    service = GeminiService()
    service.is_configured = True

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "intent_type": "PURCHASE_INTENT",
        "module": "PERSONAL",
        "requested_products": ["Ergonomic Mechanical Keyboard"],
        "product_categories": ["Peripherals"],
        "preferences": ["Bluetooth", "Brown switches"],
        "requested_quantity": 1,
        "max_budget_cents": 12000,
        "currency": "USD",
        "delivery_preferences": "Priority",
        "missing_information": [],
        "clarification_questions": [],
        "is_ambiguous": False,
        "confidence_score": 0.99,
        "source": "gemini",
        "quarantine_flags": [],
    })
    mock_client.models.generate_content.return_value = mock_response
    service.client = mock_client

    result = await service.extract_intent("Keychron keyboard under $120")
    assert result.source == "gemini"
    assert result.max_budget_cents == 12000
    assert "Ergonomic Mechanical Keyboard" in result.requested_products

@pytest.mark.asyncio
async def test_mocked_gemini_client_proposal_generation():
    service = GeminiService()
    service.is_configured = True

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "proposal_id": "prop-gemini-live-01",
        "user_id": "usr-alex-01",
        "module": "PERSONAL",
        "user_intent": "Buy keyboard",
        "merchant_id": "m-keychron-01",
        "merchant_name": "Keychron Official",
        "items": [{
            "product_id": "prod-k3pro-01",
            "name": "Keychron K3 Pro",
            "sku": "k3p-red-01",
            "category": "Peripherals",
            "quantity": 1,
            "unit_price_cents": 8400,
            "is_verified_price": True,
        }],
        "subtotal_cents": 8400,
        "tax_cents": 672,
        "shipping_cents": 0,
        "total_cents": 9072,
        "currency": "USD",
        "is_transaction_ready": True,
        "missing_information": [],
        "assumptions": ["Standard 8% tax applied"],
        "ai_reasoning": "Live Gemini generation match.",
        "source": "gemini",
        "quarantine_flags": [],
    })
    mock_client.models.generate_content.return_value = mock_response
    service.client = mock_client

    prop = await service.generate_proposal("Buy Keychron K3 Pro")
    assert prop.source == "gemini"
    assert prop.total_cents == 9072
    assert prop.is_transaction_ready is True

@pytest.mark.asyncio
async def test_mocked_gemini_client_malformed_json_falls_back():
    """
    If the LLM returns malformed or invalid JSON, service must fall back safely without crashing.
    """
    service = GeminiService()
    service.is_configured = True

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "{ INVALID_JSON_UNCLOSED "
    mock_client.models.generate_content.return_value = mock_response
    service.client = mock_client

    result = await service.extract_intent("Keychron keyboard under $100")
    assert result.source == "simulated_fallback"
    assert "Peripherals" in result.product_categories
