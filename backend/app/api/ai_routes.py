import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.enums import ModuleScope
from app.models.entities import BusinessProfile, Invoice, CareProfile, ReorderSchedule
from app.ai.gemini_service import ai_service
from app.ai.schemas import (
    IntentExtractionRequest,
    ShoppingIntentExtraction,
    ProposalGenerationRequest,
    AIPurchaseProposal,
    CashflowExplanationRequest,
    BusinessAffordabilityAnalysis,
    CareAssistRequest,
    CareReorderAssistance,
    AIHealthStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["Gemini AI Orchestrator"])

@router.get("/status", response_model=AIHealthStatus)
async def get_ai_status():
    """
    Returns the real-time operational status of the Gemini AI orchestrator.
    Transparently informs frontend whether live Gemini or fallback demo mode is active.
    """
    return ai_service.get_health_status()

@router.post("/interpret", response_model=ShoppingIntentExtraction)
async def interpret_user_intent(req: IntentExtractionRequest):
    """
    Extracts structured shopping parameters from natural-language queries.
    Never invents missing budgets and flags ambiguous queries.
    """
    module = req.module or ModuleScope.PERSONAL
    return await ai_service.extract_intent(
        query=req.query,
        user_id=req.user_id or "usr-alex-01",
        module=module,
    )

@router.post("/propose", response_model=AIPurchaseProposal)
async def draft_purchase_proposal(req: ProposalGenerationRequest):
    """
    Drafts a structured purchase proposal from user intent and merchant text.
    Quarantines untrusted merchant descriptions and detects prompt-injection commands.
    """
    module = req.module or ModuleScope.PERSONAL
    return await ai_service.generate_proposal(
        query=req.query,
        user_id=req.user_id or "usr-alex-01",
        module=module,
        care_profile_id=req.care_profile_id,
        untrusted_merchant_text=req.untrusted_merchant_text,
    )

@router.post("/explain-cashflow", response_model=BusinessAffordabilityAnalysis)
async def explain_business_cashflow(
    req: CashflowExplanationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Explains business liquidity, invoice obligations, and purchase runway in plain business terms.
    Totals are calculated deterministically in Python from persisted database records.
    """
    # Query persisted business profile
    result = await db.execute(select(BusinessProfile))
    bp = result.scalars().first()

    starting_balance = bp.operating_cash_cents if bp else 1420000

    # Query invoices
    inv_result = await db.execute(select(Invoice))
    all_invoices = inv_result.scalars().all()

    payables: List[Dict[str, Any]] = []
    receivables: List[Dict[str, Any]] = []

    if all_invoices:
        for inv in all_invoices:
            row = {
                "id": inv.id,
                "counterparty": inv.counterparty,
                "amount_cents": inv.amount_cents,
                "due_date": inv.due_date.isoformat() if hasattr(inv.due_date, "isoformat") else str(inv.due_date),
                "category": inv.category,
            }
            if inv.invoice_type.upper() == "RECEIVABLE":
                receivables.append(row)
            else:
                payables.append(row)
    else:
        # High-fidelity synthetic fallback matching Acme Design Studio demo dataset
        payables = [
            {"id": "inv-p-01", "counterparty": "AWS Cloud Infrastructure", "amount_cents": 48000, "category": "Hosting"},
            {"id": "inv-p-02", "counterparty": "Downtown Studio Lease", "amount_cents": 180000, "category": "Rent"},
            {"id": "inv-p-03", "counterparty": "Design Software Subscriptions", "amount_cents": 150000, "category": "SaaS"},
        ]
        receivables = [
            {"id": "inv-r-01", "counterparty": "Apex FinTech Rebrand Contract", "amount_cents": 345000, "category": "Consulting"},
            {"id": "inv-r-02", "counterparty": "Global Logistics UX Audit", "amount_cents": 310000, "category": "Consulting"},
        ]

    return await ai_service.explain_business_cashflow(
        starting_balance_cents=starting_balance,
        payables=payables,
        receivables=receivables,
        query=req.query,
    )

@router.post("/care-assist", response_model=CareReorderAssistance)
async def assist_care_reorder(
    req: CareAssistRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Assists care recipients and guardians with essential reorder schedules.
    Enforces strict safety boundaries: never prescribes medication, changes dosages,
    or bypasses guardian approval.
    """
    care_profile_id = req.care_profile_id or "care-eleanor-01"

    # Query care profile
    cp_res = await db.execute(select(CareProfile).where(CareProfile.id == care_profile_id))
    cp = cp_res.scalar_one_or_none()

    recipient_name = cp.recipient_name if cp else "Eleanor Vance"
    care_dict = {
        "id": care_profile_id,
        "recipient_name": recipient_name,
        "care_tier": "Assisted Living (Tier 2)",
    }

    # Query reorder schedules
    sched_res = await db.execute(
        select(ReorderSchedule).where(ReorderSchedule.care_profile_id == care_profile_id)
    )
    schedules = sched_res.scalars().all()

    essential_items: List[Dict[str, Any]] = []
    active_sched_dict = None

    if schedules:
        for s in schedules:
            item_data = {
                "id": s.id,
                "name": s.product_name,
                "category": s.category,
                "merchant": s.preferred_merchant,
                "unit_cost_cents": s.unit_cost_cents,
                "days_remaining": s.days_remaining,
                "auto_reorder_eligible": s.auto_reorder_eligible,
            }
            essential_items.append(item_data)
            if s.auto_reorder_eligible and active_sched_dict is None:
                active_sched_dict = {"id": s.id, "is_active": True, "product_name": s.product_name}
    else:
        # Realistic synthetic care essentials for Eleanor
        essential_items = [
            {
                "id": "item-cgm",
                "name": "Continuous Glucose Monitor Sensor Pods (14-day)",
                "category": "Medical Supplies",
                "merchant": "CareRx Pharmacy Sandbox",
                "unit_cost_cents": 3250,
                "days_remaining": 3,
                "auto_reorder_eligible": True,
            },
            {
                "id": "item-strips",
                "name": "Precision Glucose Test Strips 50ct",
                "category": "Medical Supplies",
                "merchant": "CareRx Pharmacy Sandbox",
                "unit_cost_cents": 1850,
                "days_remaining": 5,
                "auto_reorder_eligible": True,
            },
            {
                "id": "item-vitamins",
                "name": "Senior Daily Multivitamin Formulations 90ct",
                "category": "Daily Essentials",
                "merchant": "CareRx Pharmacy Sandbox",
                "unit_cost_cents": 1400,
                "days_remaining": 12,
                "auto_reorder_eligible": False,
            },
        ]
        active_sched_dict = {"id": "sched-eleanor-01", "is_active": True}

    return await ai_service.assist_care_reorder(
        care_profile=care_dict,
        essential_items=essential_items,
        reorder_schedule=active_sched_dict,
        query=req.query,
    )
