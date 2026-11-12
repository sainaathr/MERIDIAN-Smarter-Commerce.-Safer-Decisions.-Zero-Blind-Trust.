import hashlib
import json
from typing import List, Dict, Any, Optional

def canonicalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize item fields into a deterministic structure."""
    return {
        "category": str(item.get("category", "")).strip().lower(),
        "name": str(item.get("name", "")).strip(),
        "product_id": str(item.get("product_id", "")).strip(),
        "quantity": int(item.get("quantity", 1)),
        "sku": str(item.get("sku", "")).strip().lower() if item.get("sku") else None,
        "total_cents": int(item.get("total_cents", item.get("quantity", 1) * item.get("unit_price_cents", 0))),
        "unit_price_cents": int(item.get("unit_price_cents", 0)),
    }

def build_canonical_payload(
    user_id: str,
    module: str,
    merchant_id: str,
    merchant_name: str,
    items: List[Dict[str, Any]],
    total_cents: int,
    currency: str,
    policy_version: int = 1,
    care_profile_id: Optional[str] = None,
    subtotal_cents: Optional[int] = None,
    tax_cents: int = 0,
    shipping_cents: int = 0,
) -> str:
    """
    Constructs a deterministic canonical JSON string representing all material transaction properties.
    Sorted keys and stable separators guarantee cryptographic stability across runs and architectures.
    """
    normalized_items = [canonicalize_item(it) for it in items]
    # Sort items deterministically by product_id, then sku, then name
    normalized_items.sort(key=lambda x: (x["product_id"], x["sku"] or "", x["name"]))

    canonical_dict = {
        "care_profile_id": str(care_profile_id).strip() if care_profile_id else None,
        "currency": str(currency).strip().upper(),
        "items": normalized_items,
        "merchant_id": str(merchant_id).strip().lower(),
        "merchant_name": str(merchant_name).strip(),
        "module": str(module).strip().upper(),
        "policy_version": int(policy_version),
        "shipping_cents": int(shipping_cents),
        "subtotal_cents": int(subtotal_cents if subtotal_cents is not None else (total_cents - tax_cents - shipping_cents)),
        "tax_cents": int(tax_cents),
        "total_cents": int(total_cents),
        "user_id": str(user_id).strip(),
    }

    # Strict compact canonical JSON with sorted keys
    return json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))

def compute_transaction_fingerprint(
    user_id: str,
    module: str,
    merchant_id: str,
    merchant_name: str,
    items: List[Dict[str, Any]],
    total_cents: int,
    currency: str,
    policy_version: int = 1,
    care_profile_id: Optional[str] = None,
    subtotal_cents: Optional[int] = None,
    tax_cents: int = 0,
    shipping_cents: int = 0,
) -> tuple[str, str]:
    """
    Returns (canonical_payload_json, sha256_hex_digest).
    """
    canonical_payload = build_canonical_payload(
        user_id=user_id,
        module=module,
        merchant_id=merchant_id,
        merchant_name=merchant_name,
        items=items,
        total_cents=total_cents,
        currency=currency,
        policy_version=policy_version,
        care_profile_id=care_profile_id,
        subtotal_cents=subtotal_cents,
        tax_cents=tax_cents,
        shipping_cents=shipping_cents,
    )
    sha256_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return canonical_payload, sha256_hash
