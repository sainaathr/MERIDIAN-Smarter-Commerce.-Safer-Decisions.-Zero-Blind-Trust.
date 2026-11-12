import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.init_db import init_database

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()

@pytest.mark.asyncio
async def test_api_generate_fingerprint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-k3pro-01",
                    "name": "Keychron K3 Pro",
                    "quantity": 1,
                    "unit_price_cents": 8400,
                }
            ],
            "total_cents": 8400,
            "currency": "USD",
            "policy_version": 1,
        }
        res = await ac.post("/api/trust/fingerprint", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "sha256_hash" in data
    assert len(data["sha256_hash"]) == 64
    assert "canonical_payload" in data

@pytest.mark.asyncio
async def test_api_create_and_get_proposal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "USB-C Fast Charging Hub",
            "merchant_id": "m-anker-01",
            "merchant_name": "Anker Direct",
            "items": [
                {
                    "product_id": "prod-anker-hub",
                    "name": "Anker 7-in-1 Hub",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 4500,
                }
            ],
            "subtotal_cents": 4500,
            "tax_cents": 360,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Suggested peripheral within user $150 limit.",
        }
        create_res = await ac.post("/api/trust/proposals", json=payload)
        assert create_res.status_code == 200
        prop = create_res.json()
        assert prop["fingerprint_sha256"] is not None
        assert prop["total_cents"] == 4860

        # Retrieve proposal
        get_res = await ac.get(f"/api/trust/proposals/{prop['id']}")
        assert get_res.status_code == 200
        assert get_res.json()["title"] == "USB-C Fast Charging Hub"

@pytest.mark.asyncio
async def test_api_evaluate_policy_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create fresh proposal to test policy evaluation deterministically
        create_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Mechanical Keyboard Eval Test",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-kbd-01",
                    "name": "Keychron K3 Pro",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 8400,
                }
            ],
            "subtotal_cents": 8400,
            "tax_cents": 672,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Quiet mechanical keyboard test.",
        }
        res_create = await ac.post("/api/trust/proposals", json=create_payload)
        assert res_create.status_code == 200
        prop_id = res_create.json()["id"]

        payload = {
            "proposal_id": prop_id,
            "acting_user_id": "usr-alex-01",
        }
        res = await ac.post("/api/trust/evaluate", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["decision"] in ["REQUIRE_APPROVAL", "ALLOW"]
        assert len(data["fingerprint_hash"]) == 64
        assert "RULE_PER_ORDER_LIMIT" in data["rules_evaluated"]

@pytest.mark.asyncio
async def test_api_approve_and_state_transition():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a fresh proposal for the transition test
        create_payload = {
            "user_id": "usr-alex-01",
            "module": "PERSONAL",
            "title": "Bluetooth Ergonomic Mouse",
            "merchant_id": "m-keychron-01",
            "merchant_name": "Keychron Official",
            "items": [
                {
                    "product_id": "prod-mouse-01",
                    "name": "Keychron M3 Wireless Mouse",
                    "category": "Peripherals",
                    "quantity": 1,
                    "unit_price_cents": 4900,
                }
            ],
            "subtotal_cents": 4900,
            "tax_cents": 392,
            "shipping_cents": 0,
            "currency": "USD",
            "ai_reasoning": "Valid peripheral request.",
        }
        create_res = await ac.post("/api/trust/proposals", json=create_payload)
        assert create_res.status_code == 200
        prop = create_res.json()
        proposal_id = prop["id"]
        hash_val = prop["fingerprint_sha256"]

        # Move to AWAITING_APPROVAL
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={
                "target_state": "POLICY_CHECK",
                "actor_id": "usr-alex-01",
                "actor_role": "SYSTEM",
            },
        )
        await ac.post(
            f"/api/trust/transactions/{proposal_id}/transition",
            json={
                "target_state": "AWAITING_APPROVAL",
                "actor_id": "usr-alex-01",
                "actor_role": "SYSTEM",
            },
        )

        # Approve
        approval_payload = {
            "proposal_id": proposal_id,
            "fingerprint_hash": hash_val,
            "approver_user_id": "usr-alex-01",
            "approval_type": "USER_DIRECT",
            "ttl_seconds": 3600,
        }
        appr_res = await ac.post("/api/trust/approve", json=approval_payload)
        assert appr_res.status_code == 200
        assert appr_res.json()["fingerprint_hash"] == hash_val

        # Transition to PAYMENT_PENDING
        trans_payload = {
            "target_state": "PAYMENT_PENDING",
            "actor_id": "usr-alex-01",
            "actor_role": "USER",
            "reason": "Proceeding to checkout",
        }
        trans_res = await ac.post(f"/api/trust/transactions/{proposal_id}/transition", json=trans_payload)
        assert trans_res.status_code == 200
        assert trans_res.json()["new_state"] == "PAYMENT_PENDING"

@pytest.mark.asyncio
async def test_api_list_policies_and_audit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        pol_res = await ac.get("/api/trust/policies")
        assert pol_res.status_code == 200
        policies = pol_res.json()
        assert len(policies) >= 2

        audit_res = await ac.get("/api/trust/audit")
        assert audit_res.status_code == 200
        audits = audit_res.json()
        assert len(audits) >= 1
