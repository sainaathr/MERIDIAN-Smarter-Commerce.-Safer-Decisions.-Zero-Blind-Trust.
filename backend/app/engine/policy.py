from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from app.models.enums import (
    PolicyDecision,
    ModuleScope,
    ApprovalMode,
)

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

class PolicyEvaluationResult:
    def __init__(
        self,
        decision: PolicyDecision,
        rules_evaluated: List[str],
        passed_checks: List[str],
        failed_checks: List[str],
        human_explanation: str,
        fingerprint_hash: str,
        policy_version: int,
        requires_approval: bool,
    ):
        self.decision = decision
        self.rules_evaluated = rules_evaluated
        self.passed_checks = passed_checks
        self.failed_checks = failed_checks
        self.human_explanation = human_explanation
        self.fingerprint_hash = fingerprint_hash
        self.policy_version = policy_version
        self.requires_approval = requires_approval
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "rules_evaluated": self.rules_evaluated,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "human_explanation": self.human_explanation,
            "fingerprint_hash": self.fingerprint_hash,
            "policy_version": self.policy_version,
            "requires_approval": self.requires_approval,
            "timestamp": self.timestamp.isoformat(),
        }

def evaluate_transaction_policy(
    user_id: str,
    module: ModuleScope,
    merchant_id: str,
    merchant_name: str,
    items: List[Dict[str, Any]],
    total_cents: int,
    currency: str,
    fingerprint_hash: str,
    computed_fingerprint_hash: str,
    policy: Optional[Dict[str, Any]],
    acting_user_id: str,
    guardian_delegation: Optional[Dict[str, Any]] = None,
    existing_approval: Optional[Dict[str, Any]] = None,
    reorder_schedule: Optional[Dict[str, Any]] = None,
    baseline_item_prices: Optional[Dict[str, int]] = None,
) -> PolicyEvaluationResult:
    """
    Deterministic policy evaluator. Zero LLM dependencies.
    Evaluates hard mathematical constraints and explicit business rules.
    """
    rules_evaluated: List[str] = []
    passed_checks: List[str] = []
    failed_checks: List[str] = []

    # --------------------------------------------------------------------------
    # Check 1: Cryptographic Fingerprint Integrity (Tampering Detection)
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_FINGERPRINT_INTEGRITY")
    if fingerprint_hash != computed_fingerprint_hash:
        failed_checks.append("FINGERPRINT_MISMATCH")
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation="SECURITY DENIAL: Transaction parameters were modified after calculation. "
                              "Cryptographic SHA-256 fingerprint mismatch detected.",
            fingerprint_hash=computed_fingerprint_hash,
            policy_version=policy.get("policy_version", 1) if policy else 1,
            requires_approval=False,
        )
    passed_checks.append("FINGERPRINT_VERIFIED")

    # --------------------------------------------------------------------------
    # Check 2: Active Consent Policy Presence & Status
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_CONSENT_POLICY_ACTIVE")
    if not policy:
        failed_checks.append("NO_POLICY_CONFIGURED")
        return PolicyEvaluationResult(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation="No pre-configured policy exists for this scope. Explicit human authorization is required.",
            fingerprint_hash=fingerprint_hash,
            policy_version=1,
            requires_approval=True,
        )

    # Check Revocation
    rules_evaluated.append("RULE_CONSENT_REVOCATION_STATUS")
    if policy.get("is_revoked", False):
        failed_checks.append("POLICY_REVOKED")
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation="SECURITY DENIAL: The applicable consent policy was revoked by the user.",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )
    passed_checks.append("POLICY_UNREVOKED")

    # Check Expiration
    rules_evaluated.append("RULE_CONSENT_EXPIRATION")
    valid_until = ensure_utc(policy.get("valid_until"))
    now_utc = datetime.now(timezone.utc)
    if valid_until and valid_until < now_utc:
        failed_checks.append("POLICY_EXPIRED")
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation="POLICY DENIAL: The consent policy has expired. A refreshed policy must be granted.",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )
    passed_checks.append("POLICY_NOT_EXPIRED")

    # --------------------------------------------------------------------------
    # Check 3: Currency Matching
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_CURRENCY_CONSISTENCY")
    policy_currency = str(policy.get("currency", "USD")).upper()
    if currency.upper() != policy_currency:
        failed_checks.append("CURRENCY_MISMATCH")
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation=f"CURRENCY DENIAL: Transaction currency ({currency}) does not match policy currency ({policy_currency}).",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )
    passed_checks.append("CURRENCY_MATCH")

    # --------------------------------------------------------------------------
    # Check 4: Merchant Whitelist (if non-empty)
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_MERCHANT_WHITELIST")
    allowed_merchants = [m.lower() for m in policy.get("allowed_merchants", [])]
    if allowed_merchants and merchant_name.lower() not in allowed_merchants and merchant_id.lower() not in allowed_merchants:
        failed_checks.append("MERCHANT_NOT_WHITELISTED")
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation=f"MERCHANT DENIAL: Merchant '{merchant_name}' is not on the user's whitelisted merchants list.",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )
    passed_checks.append("MERCHANT_WHITELIST_PASSED")

    # --------------------------------------------------------------------------
    # Check 5: Category Restrictions
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_CATEGORY_RESTRICTIONS")
    allowed_categories = [c.lower() for c in policy.get("allowed_categories", [])]
    if allowed_categories:
        for item in items:
            cat = str(item.get("category", "")).lower()
            if cat and cat not in allowed_categories:
                failed_checks.append(f"CATEGORY_NOT_ALLOWED_{cat.upper()}")
                return PolicyEvaluationResult(
                    decision=PolicyDecision.DENY,
                    rules_evaluated=rules_evaluated,
                    passed_checks=passed_checks,
                    failed_checks=failed_checks,
                    human_explanation=f"CATEGORY DENIAL: Line item '{item.get('name')}' belongs to category '{cat}' which is not permitted.",
                    fingerprint_hash=fingerprint_hash,
                    policy_version=policy.get("policy_version", 1),
                    requires_approval=False,
                )
    passed_checks.append("CATEGORIES_ALLOWED")

    # --------------------------------------------------------------------------
    # Check 5B: Approved Products Whitelist (if configured in policy)
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_PRODUCT_WHITELIST")
    allowed_products = [p.lower() for p in policy.get("allowed_products", [])]
    if allowed_products:
        for item in items:
            prod_id = str(item.get("product_id", "")).lower()
            prod_name = str(item.get("name", "")).lower()
            if prod_id not in allowed_products and prod_name not in allowed_products:
                failed_checks.append("PRODUCT_NOT_APPROVED")
                return PolicyEvaluationResult(
                    decision=PolicyDecision.DENY,
                    rules_evaluated=rules_evaluated,
                    passed_checks=passed_checks,
                    failed_checks=failed_checks,
                    human_explanation=f"PRODUCT DENIAL: Product '{item.get('name')}' ({item.get('product_id')}) is not in the pre-approved product catalog.",
                    fingerprint_hash=fingerprint_hash,
                    policy_version=policy.get("policy_version", 1),
                    requires_approval=False,
                )
    passed_checks.append("PRODUCTS_APPROVED")

    # --------------------------------------------------------------------------
    # Check 5C: Line Item Quantity Restrictions
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_QUANTITY_RESTRICTIONS")
    max_qty_per_item = policy.get("max_quantity_per_item")
    if max_qty_per_item is not None and int(max_qty_per_item) > 0:
        for item in items:
            qty = int(item.get("quantity", 1))
            if qty > int(max_qty_per_item):
                failed_checks.append("QUANTITY_LIMIT_EXCEEDED")
                return PolicyEvaluationResult(
                    decision=PolicyDecision.DENY,
                    rules_evaluated=rules_evaluated,
                    passed_checks=passed_checks,
                    failed_checks=failed_checks,
                    human_explanation=f"QUANTITY DENIAL: Line item '{item.get('name')}' quantity ({qty}) exceeds max permitted ({max_qty_per_item}).",
                    fingerprint_hash=fingerprint_hash,
                    policy_version=policy.get("policy_version", 1),
                    requires_approval=False,
                )
    passed_checks.append("QUANTITY_RESTRICTIONS_SATISFIED")

    # --------------------------------------------------------------------------
    # Check 5D: Approved Reorder Schedule (CARE Mode B requirement)
    # --------------------------------------------------------------------------
    if policy.get("requires_reorder_schedule", False):
        rules_evaluated.append("RULE_REORDER_SCHEDULE_ACTIVE")
        if not reorder_schedule or not reorder_schedule.get("is_active", False):
            failed_checks.append("REORDER_SCHEDULE_INACTIVE")
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                rules_evaluated=rules_evaluated,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                human_explanation="SCHEDULE DENIAL: Autonomous reorder is restricted to an active guardian-approved reorder schedule.",
                fingerprint_hash=fingerprint_hash,
                policy_version=policy.get("policy_version", 1),
                requires_approval=False,
            )
        passed_checks.append("REORDER_SCHEDULE_SATISFIED")

    # --------------------------------------------------------------------------
    # Check 5E: Price Tolerance Ceiling
    # --------------------------------------------------------------------------
    price_tolerance_pct = policy.get("price_tolerance_pct")
    if price_tolerance_pct is not None and baseline_item_prices:
        rules_evaluated.append("RULE_PRICE_TOLERANCE")
        tolerance_factor = 1.0 + (float(price_tolerance_pct) / 100.0)
        for item in items:
            p_id = item.get("product_id")
            unit_price = item.get("unit_price_cents", 0)
            if p_id in baseline_item_prices:
                baseline = baseline_item_prices[p_id]
                max_tolerated = int(baseline * tolerance_factor)
                if unit_price > max_tolerated:
                    failed_checks.append("PRICE_TOLERANCE_EXCEEDED")
                    return PolicyEvaluationResult(
                        decision=PolicyDecision.DENY,
                        rules_evaluated=rules_evaluated,
                        passed_checks=passed_checks,
                        failed_checks=failed_checks,
                        human_explanation=f"TOLERANCE DENIAL: Price for '{item.get('name')}' (${unit_price / 100:.2f}) exceeds baseline (${baseline / 100:.2f}) beyond {price_tolerance_pct}% tolerance.",
                        fingerprint_hash=fingerprint_hash,
                        policy_version=policy.get("policy_version", 1),
                        requires_approval=False,
                    )
        passed_checks.append("PRICE_TOLERANCE_SATISFIED")

    # --------------------------------------------------------------------------
    # Check 6: Spending Ceilings (Per-Order and Monthly Remaining)
    # --------------------------------------------------------------------------
    rules_evaluated.append("RULE_PER_ORDER_LIMIT")
    max_per_order = int(policy.get("max_per_order_cents", 0))
    if max_per_order > 0 and total_cents > max_per_order:
        failed_checks.append("EXCEEDS_PER_ORDER_CAP")
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation=f"LIMIT DENIAL: Transaction total (${total_cents / 100:.2f}) exceeds the configured per-order limit (${max_per_order / 100:.2f}).",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )
    passed_checks.append("PER_ORDER_CAP_SATISFIED")

    rules_evaluated.append("RULE_MONTHLY_BUDGET_CEILING")
    monthly_budget = int(policy.get("monthly_budget_cents", 0))
    monthly_spent = int(policy.get("monthly_spent_cents", 0))
    monthly_reserved = int(policy.get("monthly_reserved_cents", 0))
    effective_total = monthly_spent + monthly_reserved + total_cents
    if monthly_budget > 0 and effective_total > monthly_budget:
        failed_checks.append("EXCEEDS_MONTHLY_BUDGET")
        if monthly_reserved > 0:
            explanation = (
                f"BUDGET DENIAL: This purchase would bring total monthly spend to ${effective_total / 100:.2f} "
                f"(including ${monthly_reserved / 100:.2f} currently held in active reservations), "
                f"exceeding the monthly authorized ceiling of ${monthly_budget / 100:.2f}."
            )
        else:
            explanation = (
                f"BUDGET DENIAL: This purchase would bring total monthly spend to ${effective_total / 100:.2f}, "
                f"exceeding the monthly authorized ceiling of ${monthly_budget / 100:.2f}."
            )
        return PolicyEvaluationResult(
            decision=PolicyDecision.DENY,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            human_explanation=explanation,
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )
    passed_checks.append("MONTHLY_BUDGET_SATISFIED")

    # --------------------------------------------------------------------------
    # Check 7: Delegated Guardian Authority Scoping (if acting as guardian)
    # --------------------------------------------------------------------------
    if guardian_delegation:
        rules_evaluated.append("RULE_GUARDIAN_AUTHORITY_SCOPE")
        if not guardian_delegation.get("can_approve", False):
            failed_checks.append("GUARDIAN_APPROVAL_REVOKED")
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                rules_evaluated=rules_evaluated,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                human_explanation="AUTHORITY DENIAL: Guardian relationship does not possess approval authority.",
                fingerprint_hash=fingerprint_hash,
                policy_version=policy.get("policy_version", 1),
                requires_approval=False,
            )
        guardian_max = int(guardian_delegation.get("max_approval_amount_cents", 0))
        if guardian_max > 0 and total_cents > guardian_max:
            failed_checks.append("EXCEEDS_GUARDIAN_DELEGATION_CEILING")
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                rules_evaluated=rules_evaluated,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                human_explanation=f"AUTHORITY DENIAL: Total (${total_cents / 100:.2f}) exceeds the guardian's delegated limit (${guardian_max / 100:.2f}).",
                fingerprint_hash=fingerprint_hash,
                policy_version=policy.get("policy_version", 1),
                requires_approval=False,
            )
        passed_checks.append("GUARDIAN_SCOPE_VALID")

    # --------------------------------------------------------------------------
    # Check 8: Approval Mode & Existing Approvals
    # --------------------------------------------------------------------------
    approval_mode = policy.get("approval_mode", ApprovalMode.EXPLICIT_ALWAYS)
    rules_evaluated.append("RULE_APPROVAL_MODE_EVALUATION")

    # Mode A: Always requires an unexpired, unrevoked explicit human approval
    if approval_mode == ApprovalMode.EXPLICIT_ALWAYS or module != ModuleScope.CARE:
        if not existing_approval:
            return PolicyEvaluationResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                rules_evaluated=rules_evaluated,
                passed_checks=passed_checks,
                failed_checks=["AWAITING_EXPLICIT_APPROVAL"],
                human_explanation="All hard policy bounds satisfied. Transaction requires explicit human approval before payment execution.",
                fingerprint_hash=fingerprint_hash,
                policy_version=policy.get("policy_version", 1),
                requires_approval=True,
            )

        # Verify existing approval
        rules_evaluated.append("RULE_APPROVAL_RECORD_VERIFICATION")
        if existing_approval.get("is_revoked", False):
            failed_checks.append("APPROVAL_WAS_REVOKED")
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                rules_evaluated=rules_evaluated,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                human_explanation="SECURITY DENIAL: Prior approval was revoked.",
                fingerprint_hash=fingerprint_hash,
                policy_version=policy.get("policy_version", 1),
                requires_approval=False,
            )

        exp = ensure_utc(existing_approval.get("expires_at"))
        if exp and exp < now_utc:
            failed_checks.append("APPROVAL_EXPIRED")
            return PolicyEvaluationResult(
                decision=PolicyDecision.DENY,
                rules_evaluated=rules_evaluated,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                human_explanation="SECURITY DENIAL: Approval has expired. Fresh human authorization is required.",
                fingerprint_hash=fingerprint_hash,
                policy_version=policy.get("policy_version", 1),
                requires_approval=True,
            )

        passed_checks.append("EXPLICIT_APPROVAL_VERIFIED")
        return PolicyEvaluationResult(
            decision=PolicyDecision.ALLOW,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=[],
            human_explanation="All policy criteria and explicit human approval satisfied. Payment execution permitted.",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )

    # Mode B (CARE Controlled Auto): Automatically authorized IF all conditions met
    if approval_mode == ApprovalMode.CONTROLLED_AUTO:
        passed_checks.append("CONTROLLED_AUTO_CONDITIONS_SATISFIED")
        return PolicyEvaluationResult(
            decision=PolicyDecision.ALLOW,
            rules_evaluated=rules_evaluated,
            passed_checks=passed_checks,
            failed_checks=[],
            human_explanation="Transaction conforms strictly to pre-authorized Mode B conditions (product, merchant, budget). Payment execution permitted.",
            fingerprint_hash=fingerprint_hash,
            policy_version=policy.get("policy_version", 1),
            requires_approval=False,
        )

    # Default fallback: safe require approval
    return PolicyEvaluationResult(
        decision=PolicyDecision.REQUIRE_APPROVAL,
        rules_evaluated=rules_evaluated,
        passed_checks=passed_checks,
        failed_checks=[],
        human_explanation="Policy evaluated. Manual confirmation required.",
        fingerprint_hash=fingerprint_hash,
        policy_version=policy.get("policy_version", 1),
        requires_approval=True,
    )
