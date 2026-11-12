import pytest
from app.engine.fingerprint import compute_transaction_fingerprint, build_canonical_payload

def test_fingerprint_determinism():
    """Identical transactions must generate identical SHA-256 hashes."""
    items = [
        {"product_id": "p-1", "name": "Mechanical Keyboard", "quantity": 1, "unit_price_cents": 8400, "total_cents": 8400},
        {"product_id": "p-2", "name": "USB-C Cable", "quantity": 2, "unit_price_cents": 1200, "total_cents": 2400},
    ]

    p1, h1 = compute_transaction_fingerprint(
        user_id="user-123",
        module="PERSONAL",
        merchant_id="m-keychron",
        merchant_name="Keychron Official",
        items=items,
        total_cents=10800,
        currency="USD",
        policy_version=1,
    )

    p2, h2 = compute_transaction_fingerprint(
        user_id="user-123",
        module="PERSONAL",
        merchant_id="m-keychron",
        merchant_name="Keychron Official",
        items=items,
        total_cents=10800,
        currency="USD",
        policy_version=1,
    )

    assert p1 == p2
    assert h1 == h2
    assert len(h1) == 64

def test_fingerprint_item_order_invariance():
    """Item ordering must be normalized deterministically so permutation doesn't alter hash."""
    items_a = [
        {"product_id": "prod-apple", "name": "Apple", "quantity": 1, "unit_price_cents": 200, "total_cents": 200},
        {"product_id": "prod-banana", "name": "Banana", "quantity": 2, "unit_price_cents": 150, "total_cents": 300},
    ]
    items_b = [
        {"product_id": "prod-banana", "name": "Banana", "quantity": 2, "unit_price_cents": 150, "total_cents": 300},
        {"product_id": "prod-apple", "name": "Apple", "quantity": 1, "unit_price_cents": 200, "total_cents": 200},
    ]

    _, h_a = compute_transaction_fingerprint(
        user_id="u1", module="PERSONAL", merchant_id="m1", merchant_name="Shop",
        items=items_a, total_cents=500, currency="USD",
    )
    _, h_b = compute_transaction_fingerprint(
        user_id="u1", module="PERSONAL", merchant_id="m1", merchant_name="Shop",
        items=items_b, total_cents=500, currency="USD",
    )
    assert h_a == h_b

def test_tampering_price_changes_hash():
    """Altering total_cents or unit_price_cents MUST change the hash."""
    base_items = [{"product_id": "p-1", "name": "Keyboard", "quantity": 1, "unit_price_cents": 8400, "total_cents": 8400}]
    _, original_hash = compute_transaction_fingerprint(
        user_id="u1", module="PERSONAL", merchant_id="m1", merchant_name="Shop",
        items=base_items, total_cents=8400, currency="USD",
    )

    # Tamper total
    _, tampered_total_hash = compute_transaction_fingerprint(
        user_id="u1", module="PERSONAL", merchant_id="m1", merchant_name="Shop",
        items=base_items, total_cents=9400, currency="USD",
    )
    assert original_hash != tampered_total_hash

    # Tamper unit price
    tampered_items = [{"product_id": "p-1", "name": "Keyboard", "quantity": 1, "unit_price_cents": 9400, "total_cents": 9400}]
    _, tampered_item_hash = compute_transaction_fingerprint(
        user_id="u1", module="PERSONAL", merchant_id="m1", merchant_name="Shop",
        items=tampered_items, total_cents=9400, currency="USD",
    )
    assert original_hash != tampered_item_hash

def test_tampering_quantity_changes_hash():
    """Altering item quantity MUST change the hash."""
    items1 = [{"product_id": "p-1", "name": "Keyboard", "quantity": 1, "unit_price_cents": 5000, "total_cents": 5000}]
    items2 = [{"product_id": "p-1", "name": "Keyboard", "quantity": 2, "unit_price_cents": 5000, "total_cents": 10000}]

    _, h1 = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items1, 5000, "USD")
    _, h2 = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items2, 10000, "USD")
    assert h1 != h2

def test_tampering_merchant_changes_hash():
    """Altering merchant id or name MUST change the hash."""
    items = [{"product_id": "p-1", "name": "Keyboard", "quantity": 1, "unit_price_cents": 5000, "total_cents": 5000}]
    _, h1 = compute_transaction_fingerprint("u1", "PERSONAL", "m-legit", "Legit Merchant", items, 5000, "USD")
    _, h2 = compute_transaction_fingerprint("u1", "PERSONAL", "m-attacker", "Attacker Store", items, 5000, "USD")
    assert h1 != h2

def test_tampering_currency_changes_hash():
    """Altering currency code MUST change the hash."""
    items = [{"product_id": "p-1", "name": "Keyboard", "quantity": 1, "unit_price_cents": 5000, "total_cents": 5000}]
    _, h1 = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items, 5000, "USD")
    _, h2 = compute_transaction_fingerprint("u1", "PERSONAL", "m1", "Shop", items, 5000, "EUR")
    assert h1 != h2

def test_tampering_user_or_care_recipient_changes_hash():
    """Altering user_id or care_profile_id MUST change the hash."""
    items = [{"product_id": "p-1", "name": "Keyboard", "quantity": 1, "unit_price_cents": 5000, "total_cents": 5000}]
    _, h1 = compute_transaction_fingerprint("user-alice", "CARE", "m1", "Shop", items, 5000, "USD", care_profile_id="care-01")
    _, h2 = compute_transaction_fingerprint("user-bob", "CARE", "m1", "Shop", items, 5000, "USD", care_profile_id="care-02")
    assert h1 != h2
