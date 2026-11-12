from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from app.models.entities import BusinessProfile, Invoice
from app.schemas.domain import (
    CashflowForecastItem,
    CashflowForecastResponse,
    BusinessAffordabilityCheckResponse,
)

def ensure_utc(dt: Any) -> datetime:
    """Ensures datetime is timezone-aware UTC."""
    if dt is None:
        return datetime.now(timezone.utc)
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def calculate_cashflow_forecast(
    business_profile: BusinessProfile,
    invoices: List[Invoice],
    horizon_days: int = 30,
    reference_time: Optional[datetime] = None,
) -> CashflowForecastResponse:
    """
    Deterministic cashflow projection engine.
    Computes a date-aware, 30-day daily cumulative cashflow forecast using strict
    integer minor unit (cents) arithmetic. Zero LLM involvement.

    Accounting Conventions:
    - Starting balance: Authoritative `operating_cash_cents` from BusinessProfile.
    - Safety floor: Authoritative `minimum_safety_reserve_cents` from BusinessProfile.
    - Eligible Invoices: Only non-PAID and non-CANCELLED invoices (PENDING, OVERDUE).
    - Overdue / Same-Day Invoices: Obligated immediately on Day 0 (t=0).
    - Future Invoices: Allocated to day index based on `due_date.date() - today.date()`.
    - Daily Cumulative Balance: B(d) = Starting Cash + Cumulative Receivables through d - Cumulative Payables through d.
    - Projected Minimum Cash: min_{d} B(d).
    - Safety Reserve Margin: Projected Minimum Cash - Safety Reserve Floor.
    """
    now = ensure_utc(reference_time) if reference_time else datetime.now(timezone.utc)
    today = now.date()

    starting_cash = int(business_profile.operating_cash_cents)
    reserve_floor = int(business_profile.minimum_safety_reserve_cents)

    # Initialize daily buckets for the forecast horizon
    daily_receivables = [0] * horizon_days
    daily_payables = [0] * horizon_days

    total_eligible_receivables = 0
    total_eligible_payables = 0

    for inv in invoices:
        status_upper = (inv.status or "").strip().upper()
        # Invariant: PAID and CANCELLED invoices are excluded from forward projections
        if status_upper in ("PAID", "CANCELLED"):
            continue

        inv_type = (inv.invoice_type or "").strip().upper()
        amount = int(inv.amount_cents)
        if amount <= 0:
            continue

        inv_due_utc = ensure_utc(inv.due_date)
        inv_due_date = inv_due_utc.date()

        # Day index calculation
        if inv_due_date <= today:
            # Overdue or due today -> immediate obligation on Day 0
            day_idx = 0
        else:
            diff_days = (inv_due_date - today).days
            if 0 <= diff_days < horizon_days:
                day_idx = diff_days
            else:
                # Beyond the 30-day forecast horizon
                day_idx = -1

        if day_idx >= 0:
            if inv_type == "RECEIVABLE":
                daily_receivables[day_idx] += amount
                total_eligible_receivables += amount
            elif inv_type == "PAYABLE":
                daily_payables[day_idx] += amount
                total_eligible_payables += amount

    # Generate cumulative daily projection
    forecast_days: List[CashflowForecastItem] = []
    running_balance = starting_cash
    projected_minimum_cash = starting_cash

    for d in range(horizon_days):
        day_date = today + timedelta(days=d)
        rec = daily_receivables[d]
        pay = daily_payables[d]
        net_change = rec - pay
        running_balance += net_change

        if running_balance < projected_minimum_cash:
            projected_minimum_cash = running_balance

        forecast_days.append(
            CashflowForecastItem(
                day=day_date.strftime("%b %d"),
                date=day_date.isoformat(),
                projected_balance_cents=running_balance,
                projected_balance=round(running_balance / 100.0, 2),
                payables_cents=pay,
                receivables_cents=rec,
                net_change_cents=net_change,
            )
        )

    safety_margin = projected_minimum_cash - reserve_floor

    # Average daily burn rate across 30 days
    avg_daily_burn = max(1, total_eligible_payables // horizon_days)
    estimated_runway_days = max(0, starting_cash // avg_daily_burn) if starting_cash > 0 else 0

    return CashflowForecastResponse(
        starting_balance_cents=starting_cash,
        starting_balance=round(starting_cash / 100.0, 2),
        minimum_safety_reserve_cents=reserve_floor,
        minimum_safety_reserve=round(reserve_floor / 100.0, 2),
        total_receivables_cents=total_eligible_receivables,
        total_payables_cents=total_eligible_payables,
        projected_minimum_cash_cents=projected_minimum_cash,
        safety_reserve_margin_cents=safety_margin,
        estimated_runway_days=estimated_runway_days,
        currency=business_profile.currency or "USD",
        forecast_days=forecast_days,
        assumptions=[
            "Eligible client receivables are collected on agreed invoice due dates.",
            "Overdue client invoices are modeled as arriving on Day 0.",
            "Scheduled payable obligations (cloud infrastructure, studio rent) are paid on due dates.",
            "PAID and CANCELLED invoices are strictly excluded from future liquidity.",
        ],
    )


def check_business_affordability(
    business_profile: BusinessProfile,
    invoices: List[Invoice],
    proposed_amount_cents: int,
    horizon_days: int = 30,
    reference_time: Optional[datetime] = None,
) -> BusinessAffordabilityCheckResponse:
    """
    Gate A — Deterministic Affordability Verification.

    Critical Invariant:
    A business purchase proposal must NOT cause projected liquid cash to drop below
    the business profile's statutory minimum safety reserve floor at any point in the
    30-day forecast horizon.

    Projected cash on day d with purchase:
      B_purchase(d) = B(d) - proposed_amount_cents
    """
    if proposed_amount_cents <= 0:
        return BusinessAffordabilityCheckResponse(
            is_affordable=False,
            reason="AFFORDABILITY REJECTION: Proposed expenditure amount must be positive.",
            starting_cash_cents=int(business_profile.operating_cash_cents),
            minimum_safety_reserve_cents=int(business_profile.minimum_safety_reserve_cents),
            projected_minimum_cash_cents=int(business_profile.operating_cash_cents),
            safety_reserve_margin_cents=int(business_profile.operating_cash_cents) - int(business_profile.minimum_safety_reserve_cents),
            proposal_amount_cents=proposed_amount_cents,
            violates_safety_reserve=True,
        )

    forecast = calculate_cashflow_forecast(
        business_profile=business_profile,
        invoices=invoices,
        horizon_days=horizon_days,
        reference_time=reference_time,
    )

    reserve_floor = forecast.minimum_safety_reserve_cents
    starting_cash = forecast.starting_balance_cents

    # Calculate minimum projected cash with the purchase deducted immediately
    # On day 0 before or after day 0 cashflows:
    min_cash_with_purchase = starting_cash - proposed_amount_cents
    for item in forecast.forecast_days:
        balance_with_purchase = item.projected_balance_cents - proposed_amount_cents
        if balance_with_purchase < min_cash_with_purchase:
            min_cash_with_purchase = balance_with_purchase

    margin_with_purchase = min_cash_with_purchase - reserve_floor
    violates_reserve = min_cash_with_purchase < reserve_floor

    if violates_reserve:
        deficit_cents = reserve_floor - min_cash_with_purchase
        reason = (
            f"AFFORDABILITY DENIAL (Gate A): Proposed expenditure of ${proposed_amount_cents / 100:,.2f} "
            f"would reduce projected minimum liquid cash to ${min_cash_with_purchase / 100:,.2f}, "
            f"breaching the statutory minimum safety reserve floor of ${reserve_floor / 100:,.2f} "
            f"by ${deficit_cents / 100:,.2f}. Purchase is blocked."
        )
        is_affordable = False
    else:
        reason = (
            f"AFFORDABILITY CONFIRMED (Gate A): Proposed expenditure of ${proposed_amount_cents / 100:,.2f} "
            f"preserves statutory safety reserve floor (${reserve_floor / 100:,.2f}). "
            f"Projected minimum cash with purchase is ${min_cash_with_purchase / 100:,.2f} "
            f"(safety margin: ${margin_with_purchase / 100:,.2f})."
        )
        is_affordable = True

    return BusinessAffordabilityCheckResponse(
        is_affordable=is_affordable,
        reason=reason,
        starting_cash_cents=starting_cash,
        minimum_safety_reserve_cents=reserve_floor,
        projected_minimum_cash_cents=min_cash_with_purchase,
        safety_reserve_margin_cents=margin_with_purchase,
        proposal_amount_cents=proposed_amount_cents,
        violates_safety_reserve=violates_reserve,
    )
