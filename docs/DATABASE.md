# MERIDIAN — Relational Database Schema Specification

## Overview
MERIDIAN uses SQLAlchemy 2.0 async ORM with SQLite for rapid local development and testing, structurally designed to transition cleanly to PostgreSQL in cloud deployments.

---

## Core Entities & Relationships

### 1. Identity & Guardianship
- **`users`**: Platform users (consumers, business owners, care recipients, caregivers).
  - `id`: UUID (Primary Key)
  - `email`: String(255), Unique, Indexed
  - `full_name`: String(255)
  - `role`: Enum (`CONSUMER`, `BUSINESS_ADMIN`, `CARE_RECIPIENT`, `CAREGIVER`, `ORGANIZER`)
  - `created_at`, `updated_at`: Timestamps
- **`care_profiles`**: Profiles for assisted commerce recipients.
  - `id`: UUID (PK)
  - `user_id`: FK -> `users.id`
  - `medical_notes_redacted`: String (Synthesized mock notes only; no real PHI)
  - `approval_mode`: Enum (`MODE_A_EVERY_ORDER`, `MODE_B_CONTROLLED_AUTO`)
  - `monthly_budget_cents`: Integer
  - `currency`: String(3), default `USD`
- **`guardian_relationships`**: Scoped permissions granting a caregiver defined oversight.
  - `id`: UUID (PK)
  - `care_profile_id`: FK -> `care_profiles.id`
  - `guardian_user_id`: FK -> `users.id`
  - `relationship_type`: String (e.g. `PRIMARY_CAREGIVER`, `FINANCIAL_DELEGATE`)
  - `can_approve`: Boolean
  - `max_approval_amount_cents`: Integer
  - `is_active`: Boolean

---

### 2. Universal Trust Engine & Policy
- **`consent_policies`**: Deterministic financial rules configured by the user/guardian.
  - `id`: UUID (PK)
  - `user_id`: FK -> `users.id`
  - `scope`: Enum (`PERSONAL`, `CARE`, `BUSINESS`, `COLLECTIVE`)
  - `max_per_order_cents`: Integer
  - `monthly_budget_cents`: Integer
  - `currency`: String(3)
  - `allowed_merchants`: JSON / Association
  - `allowed_categories`: JSON
  - `valid_until`: Timestamp
  - `is_revoked`: Boolean
  - `policy_version`: Integer
- **`approved_merchants`** / **`approved_products`**: Whitelist constraints for automated reorders.

---

### 3. Proposals, Fingerprints & Transactions
- **`purchase_proposals`**: AI-generated purchase intents before any approval.
  - `id`: UUID (PK)
  - `user_id`: FK -> `users.id`
  - `module`: Enum (`PERSONAL`, `BUSINESS`, `COLLECTIVE`, `CARE`)
  - `merchant_id`: String
  - `merchant_name`: String
  - `item_summary`: String
  - `total_cents`: Integer
  - `currency`: String(3)
  - `ai_reasoning`: Text
  - `status`: Enum (Lifecycle states)
- **`purchase_items`**: Line items within a proposal.
  - `id`: UUID (PK)
  - `proposal_id`: FK -> `purchase_proposals.id`
  - `product_id`: String
  - `name`: String
  - `quantity`: Integer
  - `unit_price_cents`: Integer
- **`transaction_fingerprints`**: Cryptographic bind (SHA-256) of material parameters.
  - `id`: UUID (PK)
  - `proposal_id`: FK -> `purchase_proposals.id`, Unique
  - `canonical_payload`: Text (Sorted JSON of user, merchant, items, quantity, price, currency)
  - `sha256_hash`: String(64), Indexed
  - `policy_version`: Integer
  - `created_at`: Timestamp
- **`approvals`**: Explicit human consent bound to an exact fingerprint.
  - `id`: UUID (PK)
  - `fingerprint_hash`: String(64), Indexed
  - `approver_user_id`: FK -> `users.id`
  - `approval_type`: Enum (`USER_DIRECT`, `GUARDIAN_DELEGATED`, `POLICY_AUTOMATIC`)
  - `expires_at`: Timestamp
  - `revoked`: Boolean
- **`transactions`**: Executed or attempted payments.
  - `id`: UUID (PK)
  - `proposal_id`: FK -> `purchase_proposals.id`
  - `state`: Enum (`DRAFT`, `PROPOSED`, `POLICY_CHECK`, `AWAITING_APPROVAL`, `APPROVED`, `PAYMENT_PENDING`, `PAID`, `REJECTED`, `EXPIRED`, `FAILED`, `CANCELLED`)
  - `final_total_cents`: Integer
  - `currency`: String(3)

---

### 4. PayPal Integration
- **`paypal_orders`**: Backend record of PayPal REST API v2 orders.
  - `id`: UUID (PK)
  - `transaction_id`: FK -> `transactions.id`, Unique
  - `paypal_order_id`: String(64), Unique, Indexed
  - `intent`: String (`CAPTURE`)
  - `status`: String (`CREATED`, `SAVED`, `APPROVED`, `VOIDED`, `COMPLETED`)
  - `approval_url`: String
- **`paypal_captures`**: Verified server-side capture responses.
  - `id`: UUID (PK)
  - `paypal_order_id`: String(64), FK -> `paypal_orders.paypal_order_id`
  - `paypal_capture_id`: String(64), Unique, Indexed
  - `status`: String (`COMPLETED`, `DECLINED`, `PENDING`)
  - `amount_cents`: Integer
  - `currency`: String(3)
  - `raw_response`: JSON

---

### 5. Audit Trail
- **`audit_events`**: Append-only tamper-evident event log.
  - `id`: UUID (PK)
  - `event_type`: Enum (`PROPOSAL_CREATED`, `POLICY_EVALUATED`, `FINGERPRINT_GENERATED`, `APPROVAL_GRANTED`, `APPROVAL_REVOKED`, `PAYPAL_ORDER_CREATED`, `PAYPAL_ORDER_CAPTURED`, `TAMPER_DETECTED`, `EMERGENCY_BLOCKED`)
  - `actor_id`: Optional UUID
  - `entity_type`: String
  - `entity_id`: String
  - `details`: JSON
  - `created_at`: Timestamp
