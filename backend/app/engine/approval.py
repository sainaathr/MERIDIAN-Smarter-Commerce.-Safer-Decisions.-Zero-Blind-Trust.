import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from app.models.enums import ApprovalType
from app.models.entities import Approval

logger = logging.getLogger("meridian.trust.approval_integrity")

def ensure_utc(dt: Optional[Any]) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

class ApprovalVerificationResult:
    def __init__(
        self,
        is_valid: bool,
        reason: str,
        reason_code: str = "APPROVAL_VALID",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.is_valid = is_valid
        self.reason = reason
        self.reason_code = reason_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "details": self.details,
        }


def record_approval_integrity_diagnostic(
    proposal_id: Optional[str] = None,
    user_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    status_before: str = "UNKNOWN",
    status_after: str = "UNKNOWN",
    is_revoked: bool = False,
    revocation_reason_code: str = "APPROVAL_VALID",
    security_signals: Optional[List[str]] = None,
    fingerprint_result: bool = True,
    consent_token_result: bool = True,
    identity_consistency_result: bool = True,
    checkout_attempt_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Records a structured diagnostic event for approval integrity decisions.
    Strictly sanitizes and omits all credentials, JWT secrets, bearer tokens,
    and PayPal client secrets.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    diagnostic_event = {
        "timestamp": now_utc,
        "proposal_id": proposal_id,
        "user_id": user_id,
        "approval_id": approval_id,
        "status_before": status_before,
        "status_after": status_after,
        "is_revoked": is_revoked,
        "revocation_reason_code": revocation_reason_code,
        "security_signals": security_signals or [],
        "fingerprint_verification": "PASS" if fingerprint_result else "FAIL",
        "consent_token_validation": "PASS" if consent_token_result else "FAIL",
        "identity_consistency": "PASS" if identity_consistency_result else "FAIL",
        "checkout_attempt_id": checkout_attempt_id,
        "metadata": {
            k: v for k, v in (details or {}).items()
            if not any(secret_kw in k.lower() for secret_kw in ("password", "secret", "token", "auth", "key", "bearer"))
        },
    }

    log_msg = f"[APPROVAL_INTEGRITY] decision={status_after} code={revocation_reason_code} proposal={proposal_id} user={user_id}"
    if revocation_reason_code in ("SUSPICIOUS_ACTIVITY_CONFIRMED", "SECURITY_BLOCK"):
        logger.error(f"{log_msg} event={json.dumps(diagnostic_event)}")
    elif not fingerprint_result or not consent_token_result or not identity_consistency_result or is_revoked:
        logger.warning(f"{log_msg} event={json.dumps(diagnostic_event)}")
    else:
        logger.info(f"{log_msg} event={json.dumps(diagnostic_event)}")

    return diagnostic_event


def verify_approval_integrity(
    approval: Optional[Dict[str, Any]],
    expected_fingerprint_hash: str,
    policy: Optional[Dict[str, Any]] = None,
    expected_user_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
    proposal_id: Optional[str] = None,
    checkout_attempt_id: Optional[str] = None,
    is_care: bool = False,
) -> ApprovalVerificationResult:
    """
    Verifies that an approval record is cryptographically and logically valid.
    Enforces that:
    1. Approval exists (APPROVAL_RECORD_MISSING if missing)
    2. Fingerprint hash matches expected hash exactly (FINGERPRINT_MISMATCH if altered)
    3. Approval is not revoked (SUSPICIOUS_ACTIVITY_CONFIRMED if marked suspicious/tampered, APPROVAL_EXPLICITLY_REVOKED if revoked)
    4. Approval has not expired (CONSENT_TOKEN_EXPIRED if expired)
    5. Underlying consent policy is not revoked/expired (CONSENT_POLICY_REVOKED / CONSENT_TOKEN_EXPIRED)
    6. Approver identity matches authorized owner or delegator (IDENTITY_MISMATCH if unauthorized)
    """
    status_before = "PENDING_VERIFICATION"
    approval_id = approval.get("id") if approval else None
    approver_id = approval.get("approver_user_id") if approval else None

    # 1. Existence check
    if not approval:
        res = ApprovalVerificationResult(
            False,
            "No approval record found.",
            reason_code="APPROVAL_RECORD_MISSING",
            details={"proposal_id": proposal_id},
        )
        record_approval_integrity_diagnostic(
            proposal_id=proposal_id,
            user_id=current_user_id or expected_user_id,
            approval_id=None,
            status_before=status_before,
            status_after="INVALID",
            is_revoked=False,
            revocation_reason_code="APPROVAL_RECORD_MISSING",
            fingerprint_result=False,
            consent_token_result=False,
            identity_consistency_result=True,
            checkout_attempt_id=checkout_attempt_id,
        )
        return res

    # 2. Fingerprint binding check
    recorded_hash = approval.get("fingerprint_hash")
    if recorded_hash != expected_fingerprint_hash:
        res = ApprovalVerificationResult(
            False,
            f"Approval hash mismatch! Recorded: {recorded_hash}, Expected: {expected_fingerprint_hash}. Prior approval invalidated by parameter change.",
            reason_code="FINGERPRINT_MISMATCH",
            details={"recorded_hash": recorded_hash, "expected_hash": expected_fingerprint_hash},
        )
        record_approval_integrity_diagnostic(
            proposal_id=proposal_id,
            user_id=current_user_id or expected_user_id,
            approval_id=approval_id,
            status_before=status_before,
            status_after="INVALID",
            is_revoked=False,
            revocation_reason_code="FINGERPRINT_MISMATCH",
            security_signals=["FINGERPRINT_HASH_MISMATCH"],
            fingerprint_result=False,
            consent_token_result=True,
            identity_consistency_result=True,
            checkout_attempt_id=checkout_attempt_id,
        )
        return res

    # 3. Explicit Revocation Check
    if approval.get("is_revoked", False):
        revoked_reason = approval.get("revoked_reason") or "No reason provided"
        reason_lower = revoked_reason.lower()
        is_suspicious = any(
            marker in reason_lower
            for marker in ("suspicious", "fraud", "tamper", "security_block", "anomaly", "quarantine")
        )
        code = "SUSPICIOUS_ACTIVITY_CONFIRMED" if is_suspicious else "APPROVAL_EXPLICITLY_REVOKED"
        res = ApprovalVerificationResult(
            False,
            f"Approval was explicitly revoked: {revoked_reason}.",
            reason_code=code,
            details={"revoked_reason": revoked_reason, "is_suspicious": is_suspicious},
        )
        record_approval_integrity_diagnostic(
            proposal_id=proposal_id,
            user_id=current_user_id or expected_user_id,
            approval_id=approval_id,
            status_before=status_before,
            status_after="REVOKED",
            is_revoked=True,
            revocation_reason_code=code,
            security_signals=["REVOCATION_FOUND", code],
            fingerprint_result=True,
            consent_token_result=False,
            identity_consistency_result=True,
            checkout_attempt_id=checkout_attempt_id,
            details={"revoked_reason": revoked_reason},
        )
        return res

    # 4. Expiration check
    expires_at = ensure_utc(approval.get("expires_at"))
    now_utc = datetime.now(timezone.utc)
    if expires_at and expires_at < now_utc:
        res = ApprovalVerificationResult(
            False,
            f"Approval expired at {expires_at.isoformat()}.",
            reason_code="CONSENT_TOKEN_EXPIRED",
            details={"expires_at": expires_at.isoformat(), "now_utc": now_utc.isoformat()},
        )
        record_approval_integrity_diagnostic(
            proposal_id=proposal_id,
            user_id=current_user_id or expected_user_id,
            approval_id=approval_id,
            status_before=status_before,
            status_after="EXPIRED",
            is_revoked=False,
            revocation_reason_code="CONSENT_TOKEN_EXPIRED",
            fingerprint_result=True,
            consent_token_result=False,
            identity_consistency_result=True,
            checkout_attempt_id=checkout_attempt_id,
        )
        return res

    # 5. Underlying consent check
    if policy:
        if policy.get("is_revoked", False):
            res = ApprovalVerificationResult(
                False,
                "Underlying consent policy was revoked, invalidating all associated approvals.",
                reason_code="CONSENT_POLICY_REVOKED",
                details={"policy": policy},
            )
            record_approval_integrity_diagnostic(
                proposal_id=proposal_id,
                user_id=current_user_id or expected_user_id,
                approval_id=approval_id,
                status_before=status_before,
                status_after="INVALID_POLICY_REVOKED",
                is_revoked=True,
                revocation_reason_code="CONSENT_POLICY_REVOKED",
                security_signals=["UNDERLYING_POLICY_REVOKED"],
                fingerprint_result=True,
                consent_token_result=False,
                identity_consistency_result=True,
                checkout_attempt_id=checkout_attempt_id,
            )
            return res

        pol_exp = ensure_utc(policy.get("expires_at") or policy.get("valid_until"))
        if pol_exp and pol_exp < now_utc:
            res = ApprovalVerificationResult(
                False,
                f"Underlying consent policy expired at {pol_exp.isoformat()}.",
                reason_code="CONSENT_TOKEN_EXPIRED",
                details={"policy_expires_at": pol_exp.isoformat()},
            )
            record_approval_integrity_diagnostic(
                proposal_id=proposal_id,
                user_id=current_user_id or expected_user_id,
                approval_id=approval_id,
                status_before=status_before,
                status_after="INVALID_POLICY_EXPIRED",
                is_revoked=False,
                revocation_reason_code="CONSENT_TOKEN_EXPIRED",
                fingerprint_result=True,
                consent_token_result=False,
                identity_consistency_result=True,
                checkout_attempt_id=checkout_attempt_id,
            )
            return res

    # 6. Identity & Ownership Binding Check
    # In Care module, guardian approvers (e.g. usr-sarah-02) legitimately approve proposals owned by care recipients (e.g. usr-eleanor-02)
    if expected_user_id and approver_id and not is_care:
        approval_type = approval.get("approval_type")
        if approval_type in ("USER_DIRECT", ApprovalType.USER_DIRECT) and approver_id != expected_user_id:
            res = ApprovalVerificationResult(
                False,
                f"Identity mismatch: Direct approval approver '{approver_id}' does not match proposal owner '{expected_user_id}'.",
                reason_code="IDENTITY_MISMATCH",
                details={"approver_id": approver_id, "expected_user_id": expected_user_id},
            )
            record_approval_integrity_diagnostic(
                proposal_id=proposal_id,
                user_id=expected_user_id,
                approval_id=approval_id,
                status_before=status_before,
                status_after="IDENTITY_REJECTED",
                is_revoked=False,
                revocation_reason_code="IDENTITY_MISMATCH",
                security_signals=["APPROVER_OWNER_MISMATCH"],
                fingerprint_result=True,
                consent_token_result=False,
                identity_consistency_result=False,
                checkout_attempt_id=checkout_attempt_id,
            )
            return res

    if not is_care and current_user_id and expected_user_id and current_user_id != expected_user_id:
        # If current checkout user does not own proposal and is not the approver
        if current_user_id != approver_id:
            res = ApprovalVerificationResult(
                False,
                f"Identity mismatch: Checkout initiator '{current_user_id}' does not own proposal and is not the authorized approver.",
                reason_code="IDENTITY_MISMATCH",
                details={"current_user_id": current_user_id, "expected_user_id": expected_user_id},
            )
            record_approval_integrity_diagnostic(
                proposal_id=proposal_id,
                user_id=current_user_id,
                approval_id=approval_id,
                status_before=status_before,
                status_after="IDENTITY_REJECTED",
                is_revoked=False,
                revocation_reason_code="IDENTITY_MISMATCH",
                security_signals=["CHECKOUT_USER_MISMATCH"],
                fingerprint_result=True,
                consent_token_result=False,
                identity_consistency_result=False,
                checkout_attempt_id=checkout_attempt_id,
            )
            return res

    # All checks passed
    res = ApprovalVerificationResult(
        True,
        "Approval is valid and cryptographically bound to transaction.",
        reason_code="APPROVAL_VALID",
        details={"approval_id": approval_id, "fingerprint_hash": expected_fingerprint_hash},
    )
    record_approval_integrity_diagnostic(
        proposal_id=proposal_id,
        user_id=current_user_id or expected_user_id,
        approval_id=approval_id,
        status_before=status_before,
        status_after="VALID",
        is_revoked=False,
        revocation_reason_code="APPROVAL_VALID",
        fingerprint_result=True,
        consent_token_result=True,
        identity_consistency_result=True,
        checkout_attempt_id=checkout_attempt_id,
    )
    return res


def verify_approver_authority(
    approver_user_id: str,
    proposal_user_id: str,
    guardian_relationship: Optional[Dict[str, Any]] = None,
    total_cents: int = 0,
    category: Optional[str] = None,
) -> ApprovalVerificationResult:
    """
    Verifies that the approver possesses valid authority to approve the given proposal.
    If approver is the proposal owner: authorized.
    If approver is a guardian: checks active status, can_approve, max_approval_amount_cents,
    and category-specific delegated permissions.
    """
    if approver_user_id == proposal_user_id:
        return ApprovalVerificationResult(True, "Primary account owner authorization confirmed.")

    if not guardian_relationship:
        return ApprovalVerificationResult(
            False,
            f"User {approver_user_id} has no registered guardian relationship with {proposal_user_id}."
        )

    if not guardian_relationship.get("is_active", True):
        return ApprovalVerificationResult(
            False,
            f"Guardian relationship for {approver_user_id} is inactive or revoked."
        )

    if not guardian_relationship.get("can_approve", False):
        return ApprovalVerificationResult(
            False,
            f"Guardian {approver_user_id} does not possess delegated approval authority."
        )

    max_amount = guardian_relationship.get("max_approval_amount_cents", 0)
    if max_amount > 0 and total_cents > max_amount:
        return ApprovalVerificationResult(
            False,
            f"Order total (${total_cents / 100:.2f}) exceeds guardian's delegated limit of ${max_amount / 100:.2f}."
        )

    # Check delegated category permissions if specified
    permissions = guardian_relationship.get("permissions")
    if permissions and category:
        matching_perm = next((p for p in permissions if p.get("category") == category), None)
        if not matching_perm or not matching_perm.get("is_active", True):
            return ApprovalVerificationResult(
                False,
                f"Guardian {approver_user_id} has no active delegated permission for category '{category}'."
            )
        perm_limit = matching_perm.get("max_per_order_cents", 0)
        if perm_limit > 0 and total_cents > perm_limit:
            return ApprovalVerificationResult(
                False,
                f"Order total (${total_cents / 100:.2f}) exceeds category delegated limit of ${perm_limit / 100:.2f}."
            )

    return ApprovalVerificationResult(True, "Delegated guardian authorization verified within authorized scope.")


def create_policy_automatic_approval(
    fingerprint_hash: str,
    approver_user_id: str,
    consent_policy_id: Optional[str] = None,
    ttl_seconds: int = 86400,
) -> Approval:
    """
    Factory for server-generated, policy-authorized automatic approval in Mode B.
    MUST ONLY be created by the trusted server-side CARE reorder workflow after a
    verified ALLOW policy verdict.
    """
    now_utc = datetime.now(timezone.utc)
    return Approval(
        fingerprint_hash=fingerprint_hash,
        approver_user_id=approver_user_id,
        approval_type=ApprovalType.POLICY_AUTOMATIC,
        consent_policy_id=consent_policy_id,
        expires_at=now_utc + timedelta(seconds=ttl_seconds),
        is_revoked=False,
    )
