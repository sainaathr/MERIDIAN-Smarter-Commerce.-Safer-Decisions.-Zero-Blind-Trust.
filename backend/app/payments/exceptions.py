"""
Payment exceptions for MERIDIAN's PayPal Sandbox integration.
Enforces fail-closed error handling and detailed audit-safe error messages.
"""

class PaymentError(Exception):
    """Base exception for all payment-related errors."""
    def __init__(self, message: str, details: dict = None, status_code: int = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.status_code = status_code

class PaymentConfigurationError(PaymentError):
    """Raised when payment environment is misconfigured or a live endpoint is detected."""
    pass

class PaymentAuthenticationError(PaymentError):
    """Raised when PayPal OAuth2 token generation or authentication fails."""
    pass

class PaymentVerificationError(PaymentError):
    """Raised when Universal Trust Engine pre-check or fingerprint validation fails."""
    pass

class PaymentPolicyDeniedError(PaymentVerificationError):
    """Raised when payment is attempted on a proposal rejected or denied by Trust Engine policy."""
    pass

class PaymentCaptureError(PaymentError):
    """Raised when PayPal order capture fails or order state is invalid."""
    pass

class ConfirmedUncapturedOrderError(PaymentCaptureError):
    """
    Explicitly and exclusively represents a PayPal order positively verified
    against PayPal Sandbox as uncaptured, valid, and eligible for controlled capture retry.
    """
    def __init__(self, message: str, order_id: str = "", order_data: dict = None):
        super().__init__(message, details=order_data or {})
        self.order_id = order_id
        self.order_data = order_data or {}

class IdempotencyWindowExpiredError(PaymentCaptureError):
    """
    Raised when order recovery cannot safely replay a request because the provider's
    idempotency retention window has expired or cannot be reliably established.
    """
    pass

# Retained for backwards-compatibility; superseded by IdempotencyWindowExpiredError
ConfirmedUncreatedOrderError = IdempotencyWindowExpiredError

class MonetaryValidationError(PaymentError, ValueError):
    """Raised when monetary string parsing, conversion, or minor-unit precision validation fails."""
    pass

class PaymentIdempotencyConflict(PaymentError):
    """Raised when duplicate or conflicting payment attempt is detected."""
    pass
