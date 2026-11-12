# MERIDIAN — Order-Creation Crash Recovery & Idempotency Architecture

## 1. Overview & Objective

In distributed commerce systems interacting with third-party payment gateways (such as PayPal Orders v2), failures during order creation present severe dual-state hazards:
- A network timeout or server process crash may occur **after** PayPal has created an order resource but **before** the local database records the PayPal order ID.
- Alternatively, a crash may occur **after** local pre-commit but **before** any network packet reaches PayPal.

Previously, transactions stranded in `ORDER_CREATION_IN_PROGRESS` required manual operator intervention or unsafe manual resets. Blind manual resets without upstream provider verification create critical payment vulnerabilities: subsequent customer retries generate new idempotency keys and duplicate provider orders, or orphaned provider orders remain authorized without local tracking.

This document specifies the deterministic, fail-closed order-creation crash recovery mechanism in MERIDIAN, aligned with the **PayPal Orders v2 Idempotency Contract**.

---

## 2. PayPal Orders v2 Idempotency Contract & Retention Windows

### 2.1 Provider Search by Request ID Does Not Exist
PayPal's REST API v2 (`POST /v2/checkout/orders`) **does not provide a search or lookup endpoint by `PayPal-Request-Id`**.
- Provider order lookup by arbitrary request ID is contractually unsupported.
- A missing result from a local in-memory lookup cache is **not proof or confirmation** that PayPal did not create the order.
- Simulation lookup (`lookup_simulated_order_by_idempotency_key`) is strictly local and segregated from live provider behavior.

### 2.2 Same-Key POST Idempotency Replay
PayPal Orders v2 guarantees idempotency via header `PayPal-Request-Id: <idempotency_key>`.
- Within the provider's active retention window, re-submitting `POST /v2/checkout/orders` with the **identical request ID** returns the existing order resource idempotently without creating a duplicate order.
- Once the retention window expires, PayPal no longer honors the idempotency key. Submitting a request with an expired key risks creating a duplicate order.

### 2.3 Retention Window Durations & Configuration
1. **Default Window (6 Hours / 21,600 Seconds)**:
   Per PayPal Orders v2 documentation, standard idempotency retention for checkout order creation defaults to **6 hours** (`PAYPAL_ORDER_IDEMPOTENCY_RETENTION_SECONDS = 21600`).
2. **Extended Window (Up to 72 Hours / 259,200 Seconds)**:
   PayPal allows account-level extension up to 72 hours by formal agreement with PayPal technical account managers.
3. **MERIDIAN Conservative Ceiling**:
   MERIDIAN enforces `PAYPAL_ORDER_IDEMPOTENCY_EXTENDED_RETENTION_CONFIRMED: bool = False` by default. Unless this flag is explicitly set to `True` in configuration, MERIDIAN caps the effective retention ceiling strictly at **6 hours** (`get_effective_order_idempotency_retention_seconds()`).
4. **Fail-Closed on Expiration**:
   If the elapsed time since the first outbound provider dispatch exceeds the verified retention window, automatic same-key POST replay is **strictly prohibited**. The transaction transitions to `MANUAL_REVIEW_REQUIRED`, retaining all budget reservations to prevent overspend.

---

## 3. Pre-Network Commit & Durable Timestamps

### 3.1 The Pre-Network Invariant
Before any outbound network request is dispatched to PayPal:
1. The transaction record must be atomically staged and committed to the database.
2. The transaction is assigned a cryptographically unique `idempotency_key` (`meridian-ord-{proposal_id}-{uuid}`) and a pre-allocated `capture_idempotency_key`.
3. `reconciliation_status` is explicitly set to `ORDER_CREATION_IN_PROGRESS`.
4. `first_dispatched_at` is durably recorded with the current UTC timestamp before any HTTP socket write.
5. The transaction record holds the proposal's cryptographic fingerprint hash (`fingerprint_sha256`) and lifecycle state `PAYMENT_PENDING`.
6. For CARE transactions, the budget reservation (`BudgetReservation`) is already in `HELD` status.

### 3.2 Durability & Immutability of `first_dispatched_at`
- `first_dispatched_at` is a dedicated timestamp column on `transactions`.
- It is **never derived** from mutable timestamps such as `updated_at`.
- It is **never reset, cleared, or overwritten** during crash recovery attempts or re-examinations.
- If a stranded transaction is missing `first_dispatched_at` (e.g., from an un-migrated legacy schema or corrupt pre-dispatch commit), automatic replay is blocked, and the transaction is immediately flagged `MANUAL_REVIEW_REQUIRED` while retaining budget reservations.

### 3.3 Persistence of Idempotency Key
The `idempotency_key` is persisted in `transactions.idempotency_key`.
- Upon crash, restart, or worker failover, recovery routines strictly load the existing persisted idempotency key.
- The system **never regenerates** an idempotency key for an existing stranded transaction.

---

## 4. Deterministic Recovery Protocol

When an incoming recovery request arrives for a stranded transaction (`ORDER_CREATION_IN_PROGRESS` or `RECONCILIATION_REQUIRED`), the engine executes the following verification flow:

```
                   Incoming Recovery Request for Proposal
                                     │
                    Check Local Transaction State & Age
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
   Age < 60s & not                                      Age >= 60s or
RECONCILIATION_REQUIRED                             RECONCILIATION_REQUIRED
           │                                                   │
  Raise 409 Conflict                                   Acquire Recovery Lock
(Active worker in-flight)                             (Atomic DB update claim)
                                                               │
                                                 Evaluate first_dispatched_at
                                                               │
                      ┌────────────────────────────────────────┴────────────────────────────────────────┐
                      ▼                                                                                 ▼
             Missing timestamp OR                                                              Within retention window
         age > retention window (6h)                                                                (age < 6h)
                      │                                                                                 │
            FAIL CLOSED IMMEDIATELY                                                          Simulation Check / Replay
       Set MANUAL_REVIEW_REQUIRED                                                                       │
        Retain HELD Reservation                                        ┌────────────────────────────────┴────────────────────────────────┐
        Write Alert Audit Event                                        ▼                                                                 ▼
        Block Outbound POST                                   Simulated Order in Local                                          Revalidate 5 Invariants &
                                                             Registry (Simulation Only)                                         Execute Same-Key POST
                                                                       │                                                                 │
                                                            Adopt Order ID & URLs                                           ┌────────────┴────────────┐
                                                            Set ORDER_CREATED                                               ▼                         ▼
                                                            Retain Reservation                                          Success:                 Timeout / 5xx:
                                                                                                                    Adopt Order ID,            RECONCILIATION_REQUIRED,
                                                                                                                    Set ORDER_CREATED          Retain Reservation
```

### 4.1 Invariants Revalidated Prior to Replay
Before issuing any outbound idempotency replay POST to PayPal, the engine strictly re-evaluates:
1. **Proposal Lifecycle & Policy Evaluation**: Confirms proposal was not denied or rejected.
2. **Cryptographic Fingerprint Integrity**: Recomputes item/price fingerprint and confirms it matches `transaction.fingerprint_sha256`.
3. **Approval Status & Expiration**: Re-verifies guardian approval is unrevoked and unexpired.
4. **Consent Policy Validity**: Re-verifies consent policy is unrevoked and unexpired.
5. **CARE Budget Reservation**: Re-verifies budget reservation exists in `HELD` status with exact matching minor units.

### 4.2 Central Safety Predicate for Reservation Releases
To eliminate dual-state financial risk, MERIDIAN implements `is_safe_to_release_care_reservation(db, proposal_id)`:
- A CARE budget reservation is **only released** if the implementation has reliable evidence that the provider order was not created or that an existing order was definitively cancelled (e.g., proposal in `AWAITING_APPROVAL` with zero dispatched transaction, or provider returning a definitive 4xx `CREATION_FAILED`).
- If authorization invariants fail during recovery of an ambiguous transaction (`ORDER_CREATION_IN_PROGRESS` or `RECONCILIATION_REQUIRED`):
  - Outbound provider POST replay is **strictly blocked**.
  - The budget reservation is **strictly retained (`HELD`)** to prevent double-spending if PayPal created the order.
  - The transaction transitions to `MANUAL_REVIEW_REQUIRED` with an alert audit log.
  - The system never infers non-creation from consent revocation or approval expiry.

---

## 5. State Transitions

| Initial State | Condition | Next State | Budget Action | Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `ORDER_CREATION_IN_PROGRESS` (<60s) | Concurrent request arrives | `ORDER_CREATION_IN_PROGRESS` | Retained | 409 Conflict |
| `ORDER_CREATION_IN_PROGRESS` (>=60s) | `first_dispatched_at` missing | `MANUAL_REVIEW_REQUIRED` | Retained | Block replay; log alert audit |
| `ORDER_CREATION_IN_PROGRESS` (>=60s) | Age > retention window (6h) | `MANUAL_REVIEW_REQUIRED` | Retained | Block replay; log alert audit |
| `ORDER_CREATION_IN_PROGRESS` (>=60s) | In retention + simulation match | `ORDER_CREATED` | Retained | Adopt simulated order |
| `ORDER_CREATION_IN_PROGRESS` (>=60s) | In retention + replay succeeds | `ORDER_CREATED` | Retained | Adopt provider order ID |
| `ORDER_CREATION_IN_PROGRESS` (>=60s) | In retention + consent revoked / expired | `MANUAL_REVIEW_REQUIRED` | **Retained** | Block replay; fail closed for manual review |
| `ORDER_CREATION_IN_PROGRESS` (>=60s) | In retention + timeout / 5xx | `RECONCILIATION_REQUIRED` | Retained | Fail closed; preserve reservation |
| `RECONCILIATION_REQUIRED` | In retention + retry succeeds | `ORDER_CREATED` | Retained | Return approval URL |
| `RECONCILIATION_REQUIRED` | Consent revoked / approval expired | `MANUAL_REVIEW_REQUIRED` | **Retained** | Block replay; preserve reservation |
| `ORDER_CREATED` | Repeated recovery call | `ORDER_CREATED` | Retained | Idempotent return (zero duplicate requests) |

---

## 6. Automatic Recovery vs. Manual Review Boundary

### Automatic Recovery Cases
The engine resolves automatically without human operator intervention when:
- Transaction is within the verified retention window (default 6h) with a valid `first_dispatched_at`.
- All 5 authorization and policy invariants revalidate successfully.
- Same-key POST idempotency replay returns HTTP 200/201 with the existing or created PayPal order.
- In-memory simulation cache contains the order (simulation mode only).
- Repeated calls query an already completed (`ORDER_CREATED`) transaction.

### Manual Review Required Cases
The engine halts automated transitions and flags `MANUAL_REVIEW_REQUIRED` when:
- `first_dispatched_at` is missing or unparseable.
- Elapsed age exceeds the verified idempotency retention window (6 hours default, up to 72 hours with explicit configuration confirmation).
- Consent policy was revoked or expired while transaction was in an ambiguous provider state.
- Guardian approval expired while transaction was in an ambiguous provider state.
- Returned order `custom_id` does not match the proposal ID (potential collision or key corruption).
- Proposal items or amounts were mutated while stranded, causing fingerprint mismatch.

---

## 7. Automated Test Coverage

The recovery architecture is verified across 27 dedicated test scenarios in `backend/tests/test_order_creation_recovery.py`:

| # | Test Scenario | Verified Invariant |
| :-: | :--- | :--- |
| 1 | Crash before provider dispatch | Revalidates invariants; safe same-key retry |
| 2 | Crash after creation before persistence | Confirmed existing simulated order adopted; 0 duplicate calls |
| 3 | Restart recovery with original key | Original key loaded from DB; never regenerated |
| 4 | Concurrent recovery attempts | Atomic SQL claim; competing worker rejected with 409 Conflict |
| 5 | Confirmed existing order validation | Validates custom_id, currency, total cents, fingerprint |
| 6 | Confirmed absence safe retry | Revalidates all invariants before retry |
| 7 | Ambiguous provider response (timeout) | Fails closed; marks RECONCILIATION_REQUIRED; retains reservation |
| 8 | Repeated recovery calls | Idempotent short-circuit on existing paypal_order_id |
| 9 | Revoked consent during ambiguous recovery | Blocks creation; marks MANUAL_REVIEW_REQUIRED; strictly retains reservation |
| 10 | Tampered fingerprint during recovery | Detects payload mutation; transitions to MANUAL_REVIEW_REQUIRED |
| 11 | Ambiguous recovery reservation hold | Reservation strictly held in HELD status during failure |
| 12 | Zero duplicate orders across retries | Repeated client retries produce at most 1 provider order |
| 13 | Explicit HTTP recovery endpoint | `POST /api/payments/{proposal_id}/recover-order` executes cleanly |
| 14 | Default retention window is 6 hours | Enforces conservative 21,600s ceiling by default |
| 15 | Extended retention requires confirmation | 72h window rejected without explicit confirmed configuration flag |
| 16 | Recovery beyond retention window fails closed | Age > 6h blocks POST, transitions to MANUAL_REVIEW_REQUIRED |
| 17 | Recovery inside retention window succeeds | Age < 6h executes same-key POST replay with original key |
| 18 | Missing `first_dispatched_at` blocks recovery | Null timestamp transitions to MANUAL_REVIEW_REQUIRED; retains reservation |
| 19 | Recovery attempts do not reset timestamp | Multiple recovery runs preserve immutable `first_dispatched_at` |
| 20 | Live mode local lookup returning None | Proves local lookup is not provider search and does not prove absence |
| 21 | Approval expired during ambiguous recovery | Blocks creation; marks MANUAL_REVIEW_REQUIRED; retains reservation |
| 22 | Proposal denied during ambiguous recovery | Blocks creation; marks MANUAL_REVIEW_REQUIRED; retains reservation |
| 23 | Consent revoked before undispatched request | Safe release via central predicate when zero transaction dispatched |
| 24 | Provider timeout followed by consent revocation | Blocks retry; marks MANUAL_REVIEW_REQUIRED; retains reservation |
| 25 | Provider 5xx followed by consent revocation | Blocks retry; marks MANUAL_REVIEW_REQUIRED; retains reservation |
| 26 | Manual review state blocks automated POST | Prohibits automated POST on MANUAL_REVIEW_REQUIRED transactions |
| 27 | Lookup method mock interception via delegation | Explicit delegation ensures mocks intercept intended recovery lookups |

All 27 tests pass with 100% reliability alongside the complete backend regression test suite.
