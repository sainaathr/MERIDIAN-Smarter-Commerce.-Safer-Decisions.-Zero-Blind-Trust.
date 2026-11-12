# MERIDIAN — REST API Contracts & Endpoint Specification

## Overview
All endpoints accept and return JSON. Authentication is token/session based. The system uses strict Pydantic v2 schemas for both request bodies and responses.

---

## 1. System & Health
- `GET /` — API root, service status and documentation link.
- `GET /api/health` — Service health, database connection state, and credentials availability check (Gemini, PayPal Sandbox).

---

## 2. Universal Trust Engine (`/api/trust/*`)
- `POST /api/trust/evaluate`
  - Input: Proposal details (items, prices, merchant, requested budget) or existing `proposal_id`.
  - Output: Structured policy decision (`ALLOW`, `REQUIRE_APPROVAL`, `DENY`), violated rules, fingerprint hash, human-readable rationale.
- `POST /api/trust/fingerprint`
  - Input: Proposal canonical payload.
  - Output: Deterministic SHA-256 fingerprint hash.
- `POST /api/trust/approve`
  - Input: `fingerprint_hash`, `approver_id`, optional note.
  - Output: Approval record or rejection if fingerprint expired/invalid.
- `GET /api/trust/proposals`
  - Query Params: `module` (e.g. `PERSONAL`, `BUSINESS`, `CARE`, `COLLECTIVE`), `limit`, `offset`.
  - Output: Ordered list of persisted purchase proposals with line items, fingerprints, and attached payment/policy status.
- `POST /api/trust/proposals`
  - Input: `ProposalCreate` (user, module, items, subtotal_cents, tax_cents, shipping_cents, currency, reasoning).
  - Validation: Strict line-item arithmetic verification (`sum(quantity * unit_price_cents) == subtotal_cents`).
  - Output: Saved proposal with bound SHA-256 fingerprint in initial state `PROPOSED`.
- `GET /api/trust/proposals/{proposal_id}`
  - Output: Authoritative proposal details, line items, cryptographic fingerprint, attached policy evaluations, and payment transactions.
- `POST /api/trust/transactions/{proposal_id}/transition`
  - Input: `target_state`, `actor_id`, `actor_role`, `reason`, `expected_version`.
  - Enforcement: Explicit deterministic transition validation. Payment-restricted state (`PAID`) forbidden via generic lifecycle.
- `GET /api/trust/audit`
  - Query Params: `severity` (`INFO`, `SUCCESS`, `WARNING`, `ALERT`), `module`, `limit`, `offset`.
  - Output: Paginated, chronological list of immutable audit events.

---

## 3. AI Orchestration (`/api/ai/*`)
- `POST /api/ai/interpret`
  - Input: `prompt` (Natural language shopping request or business question).
  - Output: Structured extraction (`intent`, `items`, `budget`, `merchant_preference`).
- `POST /api/ai/proposal`
  - Input: User request + catalog items.
  - Output: Formatted purchase proposal with pricing and justification.
- `POST /api/ai/explain`
  - Input: Policy decision or cashflow state.
  - Output: Plain-language, transparent summary without financial guarantee claims.

---

## 4. Payment Gateway & PayPal Adapter (`/api/payments/*`)
- `GET /api/payments/status`
  - Output: PayPal client connection status, environment mode, and simulation flag.
- `GET /api/payments/transactions`
  - Query Params: `limit`, `offset`.
  - Output: Authoritative list of transaction records with provider IDs, capture IDs, and simulation indicators.
- `POST /api/payments/{proposal_id}/create-order`
  - Input: `proposal_id`, optional `idempotency_key`.
  - Behavior: Validates backend state (`APPROVED`) -> checks approved fingerprint -> calls PayPal Orders API v2 -> returns `order_id` and sandbox `approval_url`.
- `POST /api/payments/{proposal_id}/capture`
  - Input: `order_id`, `proposal_id`, optional `idempotency_key`.
  - Behavior: Re-computes fingerprint -> verifies active approval -> executes database atomic claim -> calls PayPal Capture API -> logs capture receipt.
- `GET /api/payments/{proposal_id}`
  - Output: Verified payment execution details from database.
- `POST /api/payments/{proposal_id}/cancel`
  - Behavior: Explicitly cancels pending payment and returns proposal to safe terminal or rollback state.

---

## 5. Commerce Modules
- **Personal (`/api/personal/*`)**: Product catalog lookup, proposal generation, protected checkout.
- **Business (`/api/business/*`)**: Cashflow entries, invoice due list, purchase recommendation.
- **Collective (`/api/collective/*`)**: Group order creation, participant invitations, commitment progress.
- **Care (`/api/care/*`)**: Care profile setup, guardian delegations, recurring reorder predictions, Mode A/B policy triggers.
