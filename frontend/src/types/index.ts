// MERIDIAN — Core Domain Types

export type TransactionLifecycleState =
  | 'DRAFT'
  | 'PROPOSED'
  | 'POLICY_CHECK'
  | 'AWAITING_APPROVAL'
  | 'APPROVED'
  | 'PAYMENT_PENDING'
  | 'PAID'
  | 'REJECTED'
  | 'EXPIRED'
  | 'FAILED'
  | 'CANCELLED';

export type ModuleType = 'PERSONAL' | 'BUSINESS' | 'COLLECTIVE' | 'CARE';

export type PolicyDecisionType = 'ALLOW' | 'REQUIRE_APPROVAL' | 'DENY';

export interface PurchaseItem {
  id: string;
  name: string;
  quantity: number;
  unit_price: number;
  total: number;
  category?: string;
  sku?: string;
}

export interface TransactionProposal {
  id: string;
  module: ModuleType;
  title: string;
  merchant_name: string;
  merchant_id: string;
  items: PurchaseItem[];
  subtotal: number;
  tax: number;
  shipping: number;
  total: number;
  currency: string;
  created_at: string;
  state: TransactionLifecycleState;
  fingerprint_sha256: string;
  ai_reasoning: string;
  policy_decision?: PolicyDecisionType;
  policy_notes?: string;
  requires_approval: boolean;
  tamper_detected?: boolean;
  paypal_order_id?: string | null;
  paypal_capture_id?: string | null;
  paypal_status?: string | null;
  is_simulated?: boolean | null;
  approval_status?: string | null;
  updated_at?: string;
}

export interface ConsentPolicy {
  id: string;
  scope: ModuleType;
  title: string;
  max_per_order: number;
  monthly_budget: number;
  monthly_spent: number;
  currency: string;
  allowed_merchants: string[];
  allowed_categories: string[];
  approval_mode: 'EXPLICIT_ALWAYS' | 'CONTROLLED_AUTO';
  valid_until: string;
  is_active: boolean;
  policy_version: number;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  event_type:
    | 'PROPOSAL_GENERATED'
    | 'PROPOSAL_CREATED'
    | 'POLICY_EVALUATION'
    | 'POLICY_EVALUATED'
    | 'FINGERPRINT_BOUND'
    | 'APPROVAL_GRANTED'
    | 'APPROVAL_REJECTED'
    | 'PAYPAL_ORDER_CREATED'
    | 'PAYPAL_CAPTURE_SUCCESS'
    | 'CAPTURE_TIMEOUT_RECONCILED'
    | 'STATE_TRANSITION'
    | 'TAMPER_ALERT'
    | 'GUARDIAN_ACTION'
    | (string & {});
  actor: string;
  role: string;
  module: ModuleType | 'TRUST_ENGINE' | (string & {});
  summary: string;
  details?: Record<string, unknown> | null;
  fingerprint?: string | null;
  severity: 'INFO' | 'SUCCESS' | 'WARNING' | 'ALERT';
}

export interface BusinessInvoice {
  id: string;
  counterparty: string;
  type: 'RECEIVABLE' | 'PAYABLE';
  amount: number;
  due_date: string;
  status: 'DRAFT' | 'PENDING' | 'OVERDUE' | 'PAID';
  category: string;
}

export interface GroupParticipant {
  id: string;
  group_purchase_id?: string;
  user_id?: string | null;
  name: string;
  email: string;
  share_amount: number;
  share_cents?: number;
  status: 'COMMITTED' | 'PAID' | 'PENDING';
  is_organizer: boolean;
}

export interface CollectiveGroup {
  id: string;
  title: string;
  description?: string;
  total_target: number;
  total_target_cents?: number;
  amount_collected: number;
  amount_collected_cents?: number;
  total_committed?: number;
  total_committed_cents?: number;
  remaining_amount?: number;
  remaining_cents?: number;
  currency: string;
  participants: GroupParticipant[];
  deadline: string;
  status: 'OPEN' | 'FUNDED' | 'PURCHASED' | 'CANCELLED';
  target_item: string;
  participant_count?: number;
  progress_percentage?: number;
}

export interface CareItem {
  id: string;
  name: string;
  category: 'MEDICATION' | 'DAILY_ESSENTIAL' | 'NUTRITION';
  frequency: string;
  days_remaining: number;
  estimated_cost: number;
  merchant: string;
  auto_reorder_eligible: boolean;
  last_ordered: string;
}

export interface CareProfile {
  id: string;
  recipient_name: string;
  relationship: string;
  approval_mode: 'MODE_A_EVERY_ORDER' | 'MODE_B_CONTROLLED_AUTO';
  monthly_budget: number;
  monthly_spent: number;
  reserved_amount?: number;
  simulated_monthly_spent?: number;
  guardians: { name: string; role: string; email: string }[];
  monitored_items: CareItem[];
}
