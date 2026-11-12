// MERIDIAN Frontend API Service Client
// Communicates with the FastAPI backend and Universal Trust Engine

import type { TransactionProposal, AuditEvent, CareProfile, CollectiveGroup, GroupParticipant, ConsentPolicy } from '../types';

const API_BASE = 'http://localhost:8000/api';

export interface AIHealthStatusResponse {
  status: 'connected' | 'simulated_fallback' | 'unavailable';
  provider: string;
  model: string;
  has_api_key: boolean;
  latency_ms?: number;
  features: string[];
  timestamp: string;
}

export interface IntentExtractionResponse {
  intent_type: string;
  module: string;
  requested_products: string[];
  product_categories: string[];
  preferences: string[];
  requested_quantity: number | null;
  max_budget_cents: number | null;
  currency: string;
  delivery_preferences: string | null;
  missing_information: string[];
  clarification_questions: string[];
  is_ambiguous: boolean;
  confidence_score: number;
  source: string;
  quarantine_flags: string[];
}

export interface AIPurchaseProposalResponse {
  proposal_id?: string;
  user_id: string;
  care_profile_id?: string | null;
  module: string;
  user_intent: string;
  merchant_id?: string | null;
  merchant_name: string;
  items: {
    product_id?: string | null;
    name: string;
    sku?: string | null;
    category?: string;
    quantity: number;
    unit_price_cents?: number | null;
    is_verified_price: boolean;
  }[];
  subtotal_cents?: number | null;
  tax_cents?: number | null;
  shipping_cents?: number | null;
  total_cents?: number | null;
  currency: string;
  is_transaction_ready: boolean;
  missing_information: string[];
  assumptions: string[];
  ai_reasoning: string;
  created_at: string;
  source: string;
  quarantine_flags: string[];
}

export interface BusinessCashflowResponse {
  summary: string;
  financial_observations: string[];
  risks: string[];
  recommendations: string[];
  runway_days_projection?: number | null;
  deterministic_figures: {
    starting_balance_cents: number;
    starting_balance_usd: string;
    total_upcoming_payables_cents: number;
    total_upcoming_payables_usd: string;
    total_expected_receivables_cents: number;
    total_expected_receivables_usd: string;
    projected_net_cents: number;
    projected_net_usd: string;
    estimated_runway_days: number;
    payables_count: number;
    receivables_count: number;
  };
  forecast_assumptions: string[];
  data_freshness_timestamp: string;
  source: string;
  disclaimer: string;
}

export interface CareAssistResponse {
  summary: string;
  recipient_name: string;
  identified_items: {
    id: string;
    name: string;
    category: string;
    merchant: string;
    unit_cost_cents: number;
    days_remaining: number;
    auto_reorder_eligible: boolean;
  }[];
  reorder_schedule_status: string;
  requires_human_guardian_review: boolean;
  missing_information: string[];
  guardian_notes: string;
  is_emergency_bypass_requested: boolean;
  safety_advisory: string;
  source: string;
}

export async function fetchAIStatus(): Promise<AIHealthStatusResponse> {
  try {
    const res = await fetch(`${API_BASE}/ai/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    return {
      status: 'simulated_fallback',
      provider: 'google-genai (mock/fallback)',
      model: 'gemini-2.0-flash (simulated)',
      has_api_key: false,
      latency_ms: 0,
      features: ['Intent Extraction', 'Structured Proposals', 'Prompt Injection Defense'],
      timestamp: new Date().toISOString(),
    };
  }
}

export async function interpretIntent(query: string, module = 'PERSONAL'): Promise<IntentExtractionResponse> {
  const res = await fetch(`${API_BASE}/ai/interpret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, module, user_id: 'usr-alex-01' }),
  });
  if (!res.ok) throw new Error(`Failed to extract intent: HTTP ${res.status}`);
  return await res.json();
}
export async function generateAIProposal(
  query: string,
  module = 'PERSONAL',
  untrustedMerchantText?: string
): Promise<AIPurchaseProposalResponse> {
  const res = await fetch(`${API_BASE}/ai/propose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      module,
      user_id: 'usr-alex-01',
      untrusted_merchant_text: untrustedMerchantText,
    }),
  });
  if (!res.ok) throw new Error(`Failed to generate proposal: HTTP ${res.status}`);
  return await res.json();
}
export async function explainBusinessCashflow(query?: string): Promise<BusinessCashflowResponse> {
  const res = await fetch(`${API_BASE}/ai/explain-cashflow`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: 'usr-alex-01',
      query: query || 'Evaluate affordability for upcoming operational commitments.',
    }),
  });
  if (!res.ok) throw new Error(`Failed to explain cashflow: HTTP ${res.status}`);
  return await res.json();
}
export async function assistCareReorder(careProfileId = 'care-eleanor-01', query?: string): Promise<CareAssistResponse> {
  const res = await fetch(`${API_BASE}/ai/care-assist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      care_profile_id: careProfileId,
      query: query || 'Review upcoming 14-day reorder schedule',
    }),
  });
  if (!res.ok) throw new Error(`Failed to evaluate care assistance: HTTP ${res.status}`);
  return await res.json();
}
export async function evaluateProposalPolicy(proposalId: string, actingUserId = 'usr-alex-01') {
  const res = await fetch(`${API_BASE}/trust/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proposal_id: proposalId, acting_user_id: actingUserId }),
  });
  if (!res.ok) throw new Error(`Failed to evaluate policy: HTTP ${res.status}`);
  return await res.json();
}
export async function submitHumanApproval(
  proposalId: string,
  fingerprintHash: string,
  approverUserIdOrType: string = 'usr-alex-01',
  approvalType: 'USER_DIRECT' | 'GUARDIAN_DELEGATED' = 'USER_DIRECT'
) {
  let approverUserId = approverUserIdOrType;
  let finalApprovalType = approvalType;

  // Handle case where caller passed approvalType as 3rd parameter
  if (approverUserIdOrType === 'USER_DIRECT' || approverUserIdOrType === 'GUARDIAN_DELEGATED') {
    finalApprovalType = approverUserIdOrType;
    approverUserId = 'usr-alex-01';
  }

  const token = getStoredToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}/trust/approve`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      proposal_id: proposalId,
      fingerprint_hash: fingerprintHash,
      approver_user_id: approverUserId,
      approval_type: finalApprovalType,
    }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to approve: HTTP ${res.status}`);
  }
  return await res.json();
}
export interface PayPalStatusResponse {
  status: 'ready' | 'simulated' | string;
  mode: string;
  base_url: string;
  is_simulated: boolean;
  client_id_configured: boolean;
  timestamp: string;
}

export interface PayPalOrderResponse {
  order_id: string;
  status: string;
  intent: string;
  approval_url?: string | null;
  total_cents: number;
  currency: string;
  proposal_id: string;
  fingerprint_sha256: string;
  is_simulated: boolean;
  created_at: string;
}

export interface PayPalCaptureResponse {
  capture_id: string;
  order_id: string;
  status: string;
  total_cents: number;
  currency: string;
  proposal_id: string;
  fingerprint_sha256: string;
  is_simulated: boolean;
  captured_at: string;
}

export interface PaymentDetailResponse {
  transaction_id: string;
  proposal_id: string;
  user_id: string;
  state: string;
  total_cents: number;
  currency: string;
  paypal_order_id?: string | null;
  paypal_capture_id?: string | null;
  paypal_status?: string | null;
  paypal_approval_url?: string | null;
  fingerprint_sha256: string;
  is_simulated: boolean;
  created_at: string;
  updated_at: string;
}

export async function getPaymentStatus(): Promise<PayPalStatusResponse> {
  const res = await fetch(`${API_BASE}/payments/status`);
  if (!res.ok) throw new Error(`Failed to fetch payment status: HTTP ${res.status}`);
  return await res.json();
}
export async function createPayPalOrder(proposalId: string, idempotencyKey?: string): Promise<PayPalOrderResponse> {
  const token = getStoredToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/payments/${proposalId}/create-order`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ proposal_id: proposalId, idempotency_key: idempotencyKey }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to create PayPal order: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function capturePayPalOrder(proposalId: string, paypalOrderId: string, idempotencyKey?: string): Promise<PayPalCaptureResponse> {
  const token = getStoredToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/payments/${proposalId}/capture`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ proposal_id: proposalId, paypal_order_id: paypalOrderId, idempotency_key: idempotencyKey }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to capture PayPal order: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function getPaymentDetails(proposalId: string): Promise<PaymentDetailResponse> {
  const res = await fetch(`${API_BASE}/payments/${proposalId}`);
  if (!res.ok) throw new Error(`Failed to fetch payment details: HTTP ${res.status}`);
  return await res.json();
}
export async function cancelPayment(proposalId: string): Promise<{ status: string; proposal_id: string }> {
  const res = await fetch(`${API_BASE}/payments/${proposalId}/cancel`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to cancel payment: HTTP ${res.status}`);
  return await res.json();
}
// ==============================================================================
// Proposal Lifecycle & Live Transaction Bridges (Phase 5)
// ==============================================================================

export interface ProposalItemCreate {
  product_id: string;
  name: string;
  sku?: string | null;
  category?: string;
  quantity: number;
  unit_price_cents: number;
}

export interface ProposalCreateRequest {
  user_id: string;
  care_profile_id?: string | null;
  module: string;
  title: string;
  merchant_id: string;
  merchant_name: string;
  items: ProposalItemCreate[];
  subtotal_cents: number;
  tax_cents: number;
  shipping_cents: number;
  currency: string;
  ai_reasoning: string;
}

export interface ProposalResponsePayload {
  id: string;
  user_id: string;
  care_profile_id?: string | null;
  module: string;
  title: string;
  merchant_name: string;
  merchant_id: string;
  subtotal_cents: number;
  tax_cents: number;
  shipping_cents: number;
  total_cents: number;
  currency: string;
  state: string;
  fingerprint_sha256: string;
  ai_reasoning: string;
  requires_approval: boolean;
  items: {
    id: string;
    product_id: string;
    name: string;
    sku?: string | null;
    category?: string;
    quantity: number;
    unit_price_cents: number;
    total_cents: number;
  }[];
  created_at: string;
  policy_decision?: string | null;
  approval_status?: string | null;
  paypal_order_id?: string | null;
  paypal_capture_id?: string | null;
  paypal_status?: string | null;
  is_simulated?: boolean | null;
  updated_at?: string | null;
}

export function mapBackendProposalToFrontend(p: ProposalResponsePayload): TransactionProposal {
  return {
    id: p.id,
    module: (p.module as any) || 'PERSONAL',
    title: p.title || 'Untitled Proposal',
    merchant_name: p.merchant_name || 'Unknown Merchant',
    merchant_id: p.merchant_id || 'unknown',
    items: (p.items || []).map((it) => ({
      id: it.id || 'item-1',
      name: it.name || 'Unnamed Item',
      quantity: it.quantity ?? 1,
      unit_price: (it.unit_price_cents ?? 0) / 100,
      total: (it.total_cents ?? 0) / 100,
      category: it.category || 'General',
      sku: it.sku || undefined,
    })),
    subtotal: (p.subtotal_cents ?? 0) / 100,
    tax: (p.tax_cents ?? 0) / 100,
    shipping: (p.shipping_cents ?? 0) / 100,
    total: (p.total_cents ?? 0) / 100,
    currency: p.currency || 'USD',
    created_at: p.created_at || new Date().toISOString(),
    state: (p.state as any) || 'PROPOSED',
    fingerprint_sha256: p.fingerprint_sha256 || '',
    ai_reasoning: p.ai_reasoning || 'No AI reasoning recorded.',
    policy_decision: (p.policy_decision as any) || undefined,
    requires_approval: p.requires_approval ?? false,
    paypal_order_id: p.paypal_order_id,
    paypal_capture_id: p.paypal_capture_id,
    paypal_status: p.paypal_status,
    is_simulated: p.is_simulated,
    approval_status: p.approval_status || undefined,
    updated_at: p.updated_at || undefined,
  };
}

export async function createProposal(data: ProposalCreateRequest): Promise<ProposalResponsePayload> {
  const res = await fetch(`${API_BASE}/trust/proposals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to create proposal: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function transitionProposalState(
  proposalId: string,
  targetState: string,
  actorId = 'usr-alex-01',
  actorRole = 'BUYER',
  reason?: string
): Promise<{ success: boolean; proposal_id: string; previous_state: string; new_state: string; current_version: number }> {
  const res = await fetch(`${API_BASE}/trust/transactions/${proposalId}/transition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      target_state: targetState,
      actor_id: actorId,
      actor_role: actorRole,
      reason: reason || `Transitioned to ${targetState}`,
    }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to transition state: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function fetchProposals(module?: string, limit = 100, status?: string): Promise<TransactionProposal[]> {
  const params = new URLSearchParams();
  if (module && module !== 'ALL') params.append('module', module);
  if (status && status !== 'ALL') params.append('status', status);
  if (limit) params.append('limit', limit.toString());

  const url = `${API_BASE}/trust/proposals${params.toString() ? `?${params.toString()}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch proposals: HTTP ${res.status}`);
  const data: ProposalResponsePayload[] = await res.json();
  return data.map(mapBackendProposalToFrontend);
}

export async function fetchProposalById(proposalId: string): Promise<TransactionProposal> {
  const res = await fetch(`${API_BASE}/trust/proposals/${proposalId}`);
  if (!res.ok) throw new Error(`Failed to fetch proposal #${proposalId}: HTTP ${res.status}`);
  const data: ProposalResponsePayload = await res.json();
  return mapBackendProposalToFrontend(data);
}

export async function rejectProposal(
  proposalId: string,
  reason = 'Explicit rejection by user/guardian',
  actorId = 'usr-alex-01',
  actorRole = 'USER'
): Promise<any> {
  return await transitionProposalState(proposalId, 'REJECTED', actorId, actorRole, reason);
}

export async function fetchTransactions(limit = 50): Promise<PaymentDetailResponse[]> {
  const res = await fetch(`${API_BASE}/payments/transactions?limit=${limit}`);
  if (!res.ok) throw new Error(`Failed to fetch transactions: HTTP ${res.status}`);
  return await res.json();
}
export async function fetchAuditEvents(severity?: string, module?: string, limit = 50): Promise<AuditEvent[]> {
  const params = new URLSearchParams();
  if (severity && severity !== 'ALL') params.append('severity', severity);
  if (module && module !== 'ALL') params.append('module', module);
  if (limit) params.append('limit', limit.toString());

  const url = `${API_BASE}/trust/audit${params.toString() ? `?${params.toString()}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch audit events: HTTP ${res.status}`);
  return await res.json();
}
export interface CareReorderResponse {
  proposal: ProposalResponsePayload;
  requires_approval: boolean;
  decision: string;
  human_explanation: string;
  order?: PayPalOrderResponse | null;
}

export async function fetchCareProfile(careProfileId = 'care-eleanor-01'): Promise<CareProfile> {
  const res = await fetch(`${API_BASE}/care/profile?care_profile_id=${encodeURIComponent(careProfileId)}`);
  if (!res.ok) throw new Error(`Failed to fetch care profile: HTTP ${res.status}`);
  return await res.json();
}
export async function updateCareMode(
  careProfileId: string,
  mode: 'MODE_A_EVERY_ORDER' | 'MODE_B_CONTROLLED_AUTO'
): Promise<CareProfile> {
  const res = await fetch(`${API_BASE}/care/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ care_profile_id: careProfileId, mode }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to update care mode: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function triggerCareReorder(
  scheduleId: string,
  careProfileId = 'care-eleanor-01',
  actorId = 'usr-alex-01'
): Promise<CareReorderResponse> {
  const res = await fetch(`${API_BASE}/care/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      schedule_id: scheduleId,
      care_profile_id: careProfileId,
      actor_id: actorId,
    }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to trigger care reorder: HTTP ${res.status}`);
  }
  return await res.json();
}
// ==============================================================================
// Business (FLOW Financial Autonomy) API
// ==============================================================================

export interface BusinessProfileResponse {
  id: string;
  user_id: string;
  company_name: string;
  operating_cash_cents: number;
  operating_cash: number;
  minimum_safety_reserve_cents: number;
  minimum_safety_reserve: number;
  currency: string;
  estimated_runway_days?: number | null;
  total_unpaid_receivables_cents: number;
  total_unpaid_payables_cents: number;
  projected_net_liquidity_cents: number;
}

export interface InvoiceResponse {
  id: string;
  business_profile_id: string;
  counterparty: string;
  invoice_type: 'RECEIVABLE' | 'PAYABLE';
  amount_cents: number;
  amount: number;
  due_date: string;
  status: 'PENDING' | 'PAID' | 'OVERDUE' | 'CANCELLED';
  category: string;
}

export interface InvoiceCreateRequest {
  counterparty: string;
  invoice_type: 'RECEIVABLE' | 'PAYABLE';
  amount_cents: number;
  due_date: string;
  category: string;
  status?: 'PENDING' | 'PAID' | 'OVERDUE' | 'CANCELLED';
}

export interface CashflowForecastItem {
  day: string;
  date: string;
  projected_balance_cents: number;
  projected_balance: number;
  payables_cents: number;
  receivables_cents: number;
  net_change_cents: number;
}

export interface CashflowForecastResponse {
  starting_balance_cents: number;
  starting_balance: number;
  minimum_safety_reserve_cents: number;
  minimum_safety_reserve: number;
  total_receivables_cents: number;
  total_payables_cents: number;
  projected_minimum_cash_cents: number;
  safety_reserve_margin_cents: number;
  estimated_runway_days: number;
  currency: string;
  forecast_days: CashflowForecastItem[];
  assumptions: string[];
}

export interface BusinessAffordabilityCheckResponse {
  is_affordable: boolean;
  reason: string;
  starting_cash_cents: number;
  minimum_safety_reserve_cents: number;
  projected_minimum_cash_cents: number;
  safety_reserve_margin_cents: number;
  proposal_amount_cents: number;
  violates_safety_reserve: boolean;
}

export interface BusinessProposalCreateRequest {
  title: string;
  merchant_id: string;
  merchant_name: string;
  items: ProposalItemCreate[];
  subtotal_cents: number;
  tax_cents: number;
  shipping_cents: number;
  currency: string;
  ai_reasoning: string;
}

export async function fetchBusinessProfile(userId = 'usr-alex-01'): Promise<BusinessProfileResponse> {
  const res = await fetch(`${API_BASE}/business/profile?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch business profile: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function fetchBusinessInvoices(params?: {
  invoice_type?: string;
  status?: string;
  limit?: number;
  offset?: number;
  user_id?: string;
}): Promise<InvoiceResponse[]> {
  const query = new URLSearchParams();
  if (params?.invoice_type) query.append('invoice_type', params.invoice_type);
  if (params?.status) query.append('status', params.status);
  if (params?.limit) query.append('limit', String(params.limit));
  if (params?.offset) query.append('offset', String(params.offset));
  if (params?.user_id) query.append('user_id', params.user_id);

  const url = `${API_BASE}/business/invoices${query.toString() ? `?${query.toString()}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch invoices: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function fetchCashflowForecast(
  horizonDays = 30,
  userId = 'usr-alex-01'
): Promise<CashflowForecastResponse> {
  const res = await fetch(
    `${API_BASE}/business/cashflow?horizon_days=${horizonDays}&user_id=${encodeURIComponent(userId)}`
  );
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch cashflow forecast: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function createBusinessInvoice(
  invoice: InvoiceCreateRequest,
  userId = 'usr-alex-01'
): Promise<InvoiceResponse> {
  const res = await fetch(`${API_BASE}/business/invoices?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(invoice),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to create invoice: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function checkBusinessAffordability(
  amountCents: number,
  userId = 'usr-alex-01'
): Promise<BusinessAffordabilityCheckResponse> {
  const res = await fetch(`${API_BASE}/business/affordability-check?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ amount_cents: amountCents }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Affordability check failed: HTTP ${res.status}`);
  }
  return await res.json();
}
export async function createBusinessProposal(
  req: BusinessProposalCreateRequest,
  userId = 'usr-alex-01'
): Promise<ProposalResponsePayload> {
  const res = await fetch(`${API_BASE}/business/proposals?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to create business proposal: HTTP ${res.status}`);
  }
  return await res.json();
}
// ==============================================================================
// 7. Collective & Shared Purchasing (COMMON) Service API
// ==============================================================================

export interface CreateGroupPayload {
  title: string;
  target_item: string;
  total_target_cents: number;
  currency?: string;
  deadline_days?: number;
  organizer_name?: string;
  organizer_email?: string;
  organizer_share_cents?: number;
}

export interface JoinGroupPayload {
  name: string;
  email: string;
  share_cents: number;
}

export function mapBackendGroupToFrontend(data: any): CollectiveGroup {
  const targetCents = Number(data.total_target_cents || 0);
  const collectedCents = Number(data.amount_collected_cents || 0);
  const committedCents = Number(data.total_committed_cents || 0);
  const remainingCents = Number(data.remaining_cents ?? Math.max(0, targetCents - collectedCents));

  const participants: GroupParticipant[] = Array.isArray(data.participants)
    ? data.participants.map((p: any) => ({
        id: p.id,
        group_purchase_id: p.group_purchase_id,
        user_id: p.user_id,
        name: p.name,
        email: p.email,
        share_amount: typeof p.share_amount === 'number' ? p.share_amount : (p.share_cents || 0) / 100,
        share_cents: p.share_cents,
        status: (p.status || 'PENDING') as 'COMMITTED' | 'PAID' | 'PENDING',
        is_organizer: Boolean(p.is_organizer),
      }))
    : [];

  let deadlineStr = '';
  if (data.deadline) {
    try {
      deadlineStr = new Date(data.deadline).toISOString().split('T')[0];
    } catch {
      deadlineStr = String(data.deadline);
    }
  }

  const progressPct = targetCents > 0
    ? Math.min(100, Math.round((committedCents / targetCents) * 100))
    : 0;

  return {
    id: data.id,
    title: data.title,
    description: `Shared equipment pool campaign for ${data.target_item}`,
    target_item: data.target_item,
    total_target: typeof data.total_target === 'number' ? data.total_target : targetCents / 100,
    total_target_cents: targetCents,
    amount_collected: typeof data.amount_collected === 'number' ? data.amount_collected : collectedCents / 100,
    amount_collected_cents: collectedCents,
    total_committed: typeof data.total_committed === 'number' ? data.total_committed : committedCents / 100,
    total_committed_cents: committedCents,
    remaining_amount: typeof data.remaining_amount === 'number' ? data.remaining_amount : remainingCents / 100,
    remaining_cents: remainingCents,
    currency: data.currency || 'USD',
    status: data.status || 'OPEN',
    deadline: deadlineStr,
    participants,
    participant_count: participants.length,
    progress_percentage: typeof data.progress_percentage === 'number' ? data.progress_percentage : progressPct,
  };
}

export async function fetchGroups(userId = 'usr-alex-01'): Promise<CollectiveGroup[]> {
  const res = await fetch(`${API_BASE}/groups?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch group campaigns: HTTP ${res.status}`);
  }
  const data = await res.json();
  return Array.isArray(data) ? data.map(mapBackendGroupToFrontend) : [];
}

export async function fetchGroupById(groupId: string, userId = 'usr-alex-01'): Promise<CollectiveGroup> {
  const res = await fetch(`${API_BASE}/groups/${encodeURIComponent(groupId)}?user_id=${encodeURIComponent(userId)}`);
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch group details: HTTP ${res.status}`);
  }
  const data = await res.json();
  return mapBackendGroupToFrontend(data);
}

export async function createGroup(payload: CreateGroupPayload, userId = 'usr-alex-01'): Promise<CollectiveGroup> {
  const res = await fetch(`${API_BASE}/groups?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to create group: HTTP ${res.status}`);
  }
  const data = await res.json();
  return mapBackendGroupToFrontend(data);
}

export async function joinGroup(groupId: string, payload: JoinGroupPayload, userId = 'usr-alex-01'): Promise<CollectiveGroup> {
  const res = await fetch(`${API_BASE}/groups/${encodeURIComponent(groupId)}/join?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to join group: HTTP ${res.status}`);
  }
  const data = await res.json();
  return mapBackendGroupToFrontend(data);
}

export async function updateGroupCommitment(
  groupId: string,
  participantId: string,
  shareCents: number,
  userId = 'usr-alex-01'
): Promise<CollectiveGroup> {
  const res = await fetch(`${API_BASE}/groups/${encodeURIComponent(groupId)}/participants/${encodeURIComponent(participantId)}?user_id=${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ share_cents: shareCents }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to update commitment: HTTP ${res.status}`);
  }
  const data = await res.json();
  return mapBackendGroupToFrontend(data);
}

export async function evaluateGroupPolicy(groupId: string, userId = 'usr-alex-01'): Promise<any> {
  const res = await fetch(`${API_BASE}/groups/${encodeURIComponent(groupId)}/evaluate-policy?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to evaluate group policy: HTTP ${res.status}`);
  }
  return await res.json();
}

export async function fetchPolicies(userId = 'usr-alex-01'): Promise<ConsentPolicy[]> {
  const url = `${API_BASE}/trust/policies?user_id=${encodeURIComponent(userId)}`;
  const res = await fetch(url);
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Failed to fetch policies: HTTP ${res.status}`);
  }
  const data = await res.json();
  return Array.isArray(data)
    ? data.map((p: any) => ({
        id: p.id,
        scope: p.scope,
        title: p.title,
        max_per_order: typeof p.max_per_order === 'number' ? p.max_per_order : (p.max_per_order_cents || 0) / 100,
        monthly_budget: typeof p.monthly_budget === 'number' ? p.monthly_budget : (p.monthly_budget_cents || 0) / 100,
        monthly_spent: typeof p.monthly_spent === 'number' ? p.monthly_spent : (p.monthly_spent_cents || 0) / 100,
        currency: p.currency || 'USD',
        allowed_merchants: p.allowed_merchants || [],
        allowed_categories: p.allowed_categories || [],
        approval_mode: p.approval_mode || 'EXPLICIT_ALWAYS',
        valid_until: p.valid_until ? String(p.valid_until) : '',
        is_active: !Boolean(p.is_revoked),
        is_revoked: Boolean(p.is_revoked),
        policy_version: p.policy_version || 1,
      }))
    : [];
}

// Authentication Service Client
export interface AuthUser {
  user_id: string;
  full_name: string;
  email: string;
  role: string;
  is_active?: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  full_name: string;
  email: string;
  role: string;
}

export const TOKEN_STORAGE_KEY = 'meridian_access_token';

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {}
}

export function clearStoredToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {}
}

export async function loginUser(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({}));
    throw new Error(errorJson.detail || `Authentication failed: HTTP ${res.status}`);
  }
  const data: LoginResponse = await res.json();
  setStoredToken(data.access_token);
  return data;
}

export async function logoutUser(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, { method: 'POST' }).catch(() => {});
  } finally {
    clearStoredToken();
  }
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  const token = getStoredToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      if (res.status === 401) {
        clearStoredToken();
      }
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn('Could not verify session with backend:', err);
    return null;
  }
}
