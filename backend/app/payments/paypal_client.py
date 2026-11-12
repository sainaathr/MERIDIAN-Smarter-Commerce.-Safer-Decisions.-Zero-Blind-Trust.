import time
import uuid
import logging
from typing import Dict, Any, List, Optional
from decimal import Decimal, InvalidOperation
import httpx

from app.core.config import settings
from app.payments.exceptions import (
    PaymentConfigurationError,
    PaymentAuthenticationError,
    PaymentCaptureError,
    MonetaryValidationError,
)

logger = logging.getLogger(__name__)

CURRENCY_MINOR_UNITS: Dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "AUD": 2,
    "JPY": 0,
}

def parse_monetary_to_minor_units(
    val: Any,
    currency: str = "USD",
    allow_zero: bool = False,
) -> int:
    """
    Parses a monetary value into integer minor units (e.g. cents) with zero floating-point arithmetic.
    Strictly validates against float instances, non-finite values (NaN, Infinity), negative values,
    zero values (unless allow_zero=True), and excessive decimal precision beyond currency minor units.
    Does not truncate or silently round.
    """
    if isinstance(val, float):
        raise MonetaryValidationError(
            "Floating-point monetary values are forbidden in authoritative payment paths."
        )

    if isinstance(val, bool) or not isinstance(val, (str, int, Decimal)):
        raise MonetaryValidationError(
            f"Invalid monetary value type: {type(val).__name__}"
        )

    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            raise MonetaryValidationError("Monetary value cannot be empty.")
    else:
        val_str = str(val)

    try:
        dec = Decimal(val_str)
    except (InvalidOperation, TypeError):
        raise MonetaryValidationError(f"Malformed monetary string: {val!r}")

    if not dec.is_finite():
        raise MonetaryValidationError(f"Non-finite monetary values are rejected: {val!r}")

    if dec < 0:
        raise MonetaryValidationError(f"Negative monetary amounts are rejected: {val!r}")

    if not allow_zero and dec == 0:
        raise MonetaryValidationError("Monetary amount must be positive and non-zero.")

    curr_upper = currency.upper()
    if curr_upper not in CURRENCY_MINOR_UNITS:
        raise MonetaryValidationError(f"Unsupported or invalid currency: {currency!r}")

    minor_digits = CURRENCY_MINOR_UNITS[curr_upper]
    scale_factor = Decimal(10) ** minor_digits
    scaled = dec * scale_factor

    # Validate that there are no fractional minor units
    if scaled != scaled.to_integral_value():
        raise MonetaryValidationError(
            f"Monetary value {val!r} has excessive fractional precision for currency {currency!r} (max {minor_digits} decimals)."
        )

    return int(scaled)

def cents_to_paypal_amount(cents: int, currency: str = "USD") -> str:
    """Converts integer minor units (e.g. cents) to exact PayPal string with zero floating-point arithmetic."""
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise MonetaryValidationError(f"Minor units must be an integer, got {type(cents).__name__}")
    if cents < 0:
        raise MonetaryValidationError("Amount cannot be negative.")
    curr_upper = currency.upper()
    if curr_upper not in CURRENCY_MINOR_UNITS:
        raise MonetaryValidationError(f"Unsupported or invalid currency: {currency!r}")
    minor_digits = CURRENCY_MINOR_UNITS[curr_upper]
    if minor_digits == 0:
        return str(cents)
    scale = 10 ** minor_digits
    major = cents // scale
    rem = cents % scale
    return f"{major}.{rem:0{minor_digits}d}"

def paypal_amount_to_cents(amount_str: Any, currency: str = "USD", allow_zero: bool = True) -> int:
    """Converts PayPal string amount to integer minor units (cents) with zero floating-point arithmetic."""
    return parse_monetary_to_minor_units(amount_str, currency=currency, allow_zero=allow_zero)

class PayPalClient:
    """
    Asynchronous PayPal REST API v2 client.
    Strictly restricted to the PayPal Developer Sandbox.
    Fails closed if live endpoints or non-sandbox configurations are detected.
    Provides transparent high-fidelity simulation when sandbox credentials are not yet supplied.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        mode: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        self.client_id = client_id or settings.PAYPAL_CLIENT_ID
        self.client_secret = client_secret or settings.PAYPAL_CLIENT_SECRET
        self.base_url = (base_url or settings.PAYPAL_BASE_URL).rstrip("/")
        self.mode = mode or settings.PAYPAL_MODE
        self.timeout_seconds = timeout_seconds or settings.PAYPAL_TIMEOUT_SECONDS
        self.max_retries = max_retries or settings.PAYPAL_MAX_RETRIES

        # Security Invariant: Refuse live endpoints
        self._validate_sandbox_safety()

        # Token cache
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        # In-memory store for simulated sandbox orders
        self._simulated_orders: Dict[str, Dict[str, Any]] = {}
        self._simulated_orders_by_idempotency: Dict[str, Dict[str, Any]] = {}

    def _validate_sandbox_safety(self) -> None:
        if self.mode.lower() != "sandbox":
            raise PaymentConfigurationError(
                f"SECURITY VIOLATION: Refusing non-sandbox mode: '{self.mode}'. MERIDIAN operates solely in PayPal Sandbox."
            )
        base_lower = self.base_url.lower()
        if "sandbox" not in base_lower and "localhost" not in base_lower and "127.0.0.1" not in base_lower:
            raise PaymentConfigurationError(
                f"SECURITY VIOLATION: Refusing live PayPal URL: '{self.base_url}'. Only sandbox endpoints permitted."
            )

    @property
    def is_simulated(self) -> bool:
        """True if credentials are not configured and client runs in high-fidelity mock mode."""
        return not (self.client_id and self.client_secret and self.client_id != "your_paypal_sandbox_client_id_here")

    async def check_connectivity(self) -> str:
        """
        Determines the true operational state of the PayPal Sandbox gateway:
        - 'unconfigured': No credentials provided.
        - 'simulated': High-fidelity mock mode active without credentials.
        - 'connected': Validated OAuth2 token retrieval against PayPal Sandbox.
        - 'unavailable': Configured credentials failed authentication or network timeout.
        """
        if self.is_simulated:
            return "simulated" if (self.client_id or not self.client_secret) else "unconfigured"

        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return "connected"

        try:
            # Test connectivity with bounded timeout of 5 seconds
            async with httpx.AsyncClient(timeout=min(float(self.timeout_seconds), 5.0)) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    headers={"Accept": "application/json"},
                    auth=(self.client_id, self.client_secret),
                )
                if resp.status_code == 200:
                    return "connected"
                return "unavailable"
        except Exception:
            return "unavailable"

    async def get_access_token(self) -> str:
        """Retrieves an OAuth2 Bearer token from PayPal Sandbox, caching until expiration."""
        if self.is_simulated:
            return "simulated_sandbox_token_" + uuid.uuid4().hex[:12]

        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        token_url = f"{self.base_url}/v1/oauth2/token"
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en_US",
        }
        data = {"grant_type": "client_credentials"}

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(
                        token_url,
                        data=data,
                        headers=headers,
                        auth=(self.client_id, self.client_secret),
                    )
                    if resp.status_code == 200:
                        payload = resp.json()
                        self._access_token = payload.get("access_token")
                        expires_in = payload.get("expires_in", 3600)
                        # Cache with safety margin of 60 seconds
                        self._token_expires_at = now + max(60, expires_in - 60)
                        return self._access_token
                    elif resp.status_code in (401, 403):
                        raise PaymentAuthenticationError(
                            f"PayPal Sandbox authentication failed: HTTP {resp.status_code} - {resp.text}"
                        )
                    else:
                        if attempt == self.max_retries:
                            raise PaymentAuthenticationError(
                                f"PayPal OAuth2 token endpoint returned HTTP {resp.status_code}: {resp.text}"
                            )
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise PaymentAuthenticationError(f"PayPal OAuth2 network failure: {str(exc)}")
            time.sleep(0.5)

        raise PaymentAuthenticationError("Unable to obtain PayPal OAuth2 access token after retries.")

    async def create_order(
        self,
        total_cents: int,
        currency: str,
        items: List[Dict[str, Any]],
        custom_id: str,
        idempotency_key: str,
        subtotal_cents: Optional[int] = None,
        tax_cents: int = 0,
        shipping_cents: int = 0,
    ) -> Dict[str, Any]:
        """
        Creates a PayPal checkout order with intent=CAPTURE.
        Enforces exact integer arithmetic converted to 2-decimal strings.
        """
        self._validate_sandbox_safety()

        if subtotal_cents is None:
            subtotal_cents = total_cents - tax_cents - shipping_cents

        total_str = cents_to_paypal_amount(total_cents)
        subtotal_str = cents_to_paypal_amount(subtotal_cents)
        tax_str = cents_to_paypal_amount(tax_cents)
        shipping_str = cents_to_paypal_amount(shipping_cents)

        order_items = []
        for item in items:
            order_items.append({
                "name": item.get("name", "Product")[:127],
                "quantity": str(item.get("quantity", 1)),
                "unit_amount": {
                    "currency_code": currency,
                    "value": cents_to_paypal_amount(item.get("unit_price_cents", 0)),
                },
                "category": "PHYSICAL_GOODS",
            })

        order_payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": custom_id,
                    "custom_id": custom_id,
                    "amount": {
                        "currency_code": currency,
                        "value": total_str,
                        "breakdown": {
                            "item_total": {"currency_code": currency, "value": subtotal_str},
                            "tax_total": {"currency_code": currency, "value": tax_str},
                            "shipping": {"currency_code": currency, "value": shipping_str},
                        },
                    },
                    "items": order_items,
                }
            ],
            "application_context": {
                "brand_name": "MERIDIAN",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": f"{settings.FRONTEND_URL}/trust-centre?status=success",
                "cancel_url": f"{settings.FRONTEND_URL}/trust-centre?status=cancelled",
            },
        }

        if self.is_simulated:
            if idempotency_key in self._simulated_orders_by_idempotency:
                return self._simulated_orders_by_idempotency[idempotency_key]

            sim_order_id = f"SIM-ORD-{uuid.uuid4().hex[:12].upper()}"
            sim_response = {
                "id": sim_order_id,
                "status": "CREATED",
                "intent": "CAPTURE",
                "create_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "links": [
                    {
                        "href": f"https://www.sandbox.paypal.com/checkoutnow?token={sim_order_id}",
                        "rel": "approve",
                        "method": "GET",
                    },
                    {
                        "href": f"{self.base_url}/v2/checkout/orders/{sim_order_id}",
                        "rel": "self",
                        "method": "GET",
                    },
                    {
                        "href": f"{self.base_url}/v2/checkout/orders/{sim_order_id}/capture",
                        "rel": "capture",
                        "method": "POST",
                    },
                ],
                "purchase_units": order_payload["purchase_units"],
                "_is_simulated": True,
            }
            self._simulated_orders[sim_order_id] = sim_response
            self._simulated_orders_by_idempotency[idempotency_key] = sim_response
            return sim_response

        token = await self.get_access_token()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": idempotency_key,
        }

        url = f"{self.base_url}/v2/checkout/orders"
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, json=order_payload, headers=headers)
                    if resp.status_code in (200, 201):
                        return resp.json()
                    elif resp.status_code in (401, 403):
                        raise PaymentAuthenticationError(
                            f"PayPal authorization rejected: HTTP {resp.status_code} - {resp.text}",
                            status_code=resp.status_code,
                        )
                    elif resp.status_code in (400, 422):
                        raise PaymentCaptureError(
                            f"PayPal order creation rejected: HTTP {resp.status_code} - {resp.text}",
                            status_code=resp.status_code,
                        )
                    elif resp.status_code == 429:
                        raise PaymentCaptureError(
                            f"PayPal rate limit exceeded: HTTP 429 - {resp.text}",
                            status_code=429,
                        )
                    elif resp.status_code == 408:
                        raise PaymentCaptureError(
                            f"PayPal request timeout: HTTP 408 - {resp.text}",
                            status_code=408,
                        )
                    elif resp.status_code >= 400 and resp.status_code < 500:
                        raise PaymentCaptureError(
                            f"PayPal client error creating order: HTTP {resp.status_code} - {resp.text}",
                            status_code=resp.status_code,
                        )
                    else:
                        if attempt == self.max_retries:
                            raise PaymentCaptureError(
                                f"PayPal server error creating order: HTTP {resp.status_code} - {resp.text}",
                                status_code=resp.status_code,
                            )
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise PaymentCaptureError(f"PayPal network error creating order: {str(exc)}")
            time.sleep(0.5)

        raise PaymentCaptureError("Failed to create PayPal order after retries.")

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Queries order status from PayPal Sandbox."""
        self._validate_sandbox_safety()

        if self.is_simulated:
            if order_id in self._simulated_orders:
                return self._simulated_orders[order_id]
            # Return synthetic approved order for simulation testing
            return {
                "id": order_id,
                "status": "APPROVED",
                "intent": "CAPTURE",
                "_is_simulated": True,
            }

        token = await self.get_access_token()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        url = f"{self.base_url}/v2/checkout/orders/{order_id}"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                raise PaymentCaptureError(f"PayPal order '{order_id}' not found.")
            else:
                raise PaymentCaptureError(f"PayPal get order failed: HTTP {resp.status_code} - {resp.text}")

    async def lookup_order_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Local in-memory lookup for simulated checkout orders only.

        IMPORTANT CONTRACTUAL DISTINCTION:
        - PayPal Orders v2 REST API DOES NOT provide an order search endpoint by PayPal-Request-Id.
        - In live or Sandbox provider modes, provider-side search by request ID is unsupported.
        - A None result from local lookup is NOT proof or positive confirmation that an order
          does not exist on PayPal.
        - Real provider crash recovery operates strictly via same-key POST idempotency replay
          within the verified retention window, never via provider search by request ID.
        """
        self._validate_sandbox_safety()

        if self.is_simulated:
            return self._simulated_orders_by_idempotency.get(idempotency_key)

        return None

    async def lookup_simulated_order_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Simulation-only local lookup. Explicitly delegates to lookup_order_by_idempotency_key
        to maintain compatibility with test mocks patching either method name while
        strictly preserving the underlying simulation-only in-memory semantics.
        """
        return await self.lookup_order_by_idempotency_key(idempotency_key)

    async def capture_order(self, order_id: str, idempotency_key: str) -> Dict[str, Any]:
        """
        Captures payment for an approved PayPal order.
        """
        self._validate_sandbox_safety()

        if self.is_simulated:
            sim_cap_id = f"SIM-CAP-{uuid.uuid4().hex[:12].upper()}"
            existing = self._simulated_orders.get(order_id, {})
            pu = existing.get("purchase_units", [{}])[0]
            amount = pu.get("amount", {"value": "0.00", "currency_code": "USD"})

            sim_capture_resp = {
                "id": order_id,
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "reference_id": pu.get("reference_id", order_id),
                        "payments": {
                            "captures": [
                                {
                                    "id": sim_cap_id,
                                    "status": "COMPLETED",
                                    "amount": amount,
                                    "final_capture": True,
                                    "create_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "update_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                }
                            ]
                        },
                    }
                ],
                "_is_simulated": True,
            }
            if order_id in self._simulated_orders:
                self._simulated_orders[order_id]["status"] = "COMPLETED"
            return sim_capture_resp

        token = await self.get_access_token()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": idempotency_key,
        }
        url = f"{self.base_url}/v2/checkout/orders/{order_id}/capture"

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, headers=headers)
                    if resp.status_code in (200, 201):
                        return resp.json()
                    elif resp.status_code in (401, 403):
                        raise PaymentAuthenticationError(f"PayPal capture authorization failed: {resp.text}")
                    elif resp.status_code >= 400 and resp.status_code < 500:
                        raise PaymentCaptureError(f"PayPal capture rejected: HTTP {resp.status_code} - {resp.text}")
                    else:
                        if attempt == self.max_retries:
                            raise PaymentCaptureError(f"PayPal server error during capture: HTTP {resp.status_code} - {resp.text}")
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise PaymentCaptureError(f"PayPal network error during capture: {str(exc)}")
            time.sleep(0.5)

        raise PaymentCaptureError("Failed to capture PayPal order after retries.")
