# MERIDIAN

> **Smarter Commerce. Safer Decisions. Zero Blind Trust.**

MERIDIAN is an autonomous commerce coordination platform built around a non-negotiable security axiom:

$$\textbf{AI proposes; deterministic policy authorizes.}$$

While generative AI models can discover merchants, structure multi-item purchase baskets, and extract user purchasing intent from natural language, they possess **zero authority to execute financial transactions**. Every proposed transaction must satisfy rigorous, tamper-evident cryptographic constraints and explicit human-governed policy rules before payment dispatch.

---

## 1. The Problem & The MERIDIAN Solution

### The Challenge of Autonomous Agentic Commerce
As autonomous AI agents evolve from conversational assistants into economic actors capable of purchasing goods and managing budgets, traditional API security models break down:
- **Prompt Injection & Adversarial Merchant Data:** Untrusted external merchant descriptions or product listings can inject malicious instructions attempting to bypass budget limits or divert funds.
- **Hallucinated Spending Authority:** Probabilistic models cannot be trusted to self-enforce strict mathematical constraints (e.g. daily/monthly spending caps or vendor whitelists).
- **Post-Approval Tampering:** If transaction parameters (price, quantity, merchant, recipient) are altered between user review and payment execution, unauthorized funds can be captured.

### The MERIDIAN Architecture
MERIDIAN introduces a strict **separation of powers**:
1. **AI Orchestrator (Google Gemini API):** Analyzes requests, discovers items, generates rationale, and synthesizes structured purchase proposals. Treated strictly as an unprivileged actor.
2. **Universal Trust Engine:** A deterministic cryptographic and rule-based kernel that generates canonical SHA-256 transaction fingerprints, evaluates deterministic policy rules, validates human consent tokens, and logs append-only audit events.
3. **Payment Gateway Integration (PayPal Developer Sandbox):** Server-side Orders API v2 integration with pre-payment cryptographic verification, idempotent authorization holds, and post-capture verification.

---

## 2. Core Autonomous Commerce Modules

MERIDIAN provides four specialized modules addressing distinct commerce workflows:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             MERIDIAN PLATFORM                               │
└──────┬──────────────────────┬──────────────────────┬────────────────────────┘
       │                      │                      │                        │
┌──────▼──────┐        ┌──────▼──────┐        ┌──────▼──────┐          ┌──────▼──────┐
│    PACT     │        │    FLOW     │        │    CARE     │          │   COMMON    │
│  Personal   │        │  Business   │        │   Assisted  │          │  Collective │
│  Commerce   │        │  Cashflow   │        │  Commerce   │          │  Purchasing │
└─────────────┘        └─────────────┘        └─────────────┘          └─────────────┘
```

### 1. PACT — Personal Autonomous Commerce Tokens
- **Mode A (Human Consent Required):** High-value or unverified merchant purchases require explicit human review and approval in the dashboard. Generates a signed, time-bounded approval token bound to the proposal's SHA-256 fingerprint.
- **Mode B (Bounded Autonomy):** Routine, pre-approved low-value purchases execute autonomously strictly within predefined per-order caps, monthly budget limits, and category allowlists.

### 2. FLOW — Business Working Capital & Cashflow Autonomy
- **Forward-Looking Cashflow Forecasting:** Analyzes 30-day cash projections and outstanding accounts payable / receivables.
- **Cash Buffer Protection:** Automatically denies or defers inventory purchases if executing payment would breach the company's minimum safe working capital buffer.
- **Invoice Reconciliation:** Correlates purchases with verified vendor invoices and terms.

### 3. CARE — Assisted Living & Essential Commerce
- **Guardian Delegation:** Allows authorized family members or caregivers to establish spending rules, approved merchant rosters, and monthly limits for care recipients.
- **Budget Reservation Holds:** Atomically holds budget allocations upon proposal approval to prevent race conditions or double-spending across concurrent scheduled reorders.
- **Strict Guardrails:** Restricts purchases to pre-screened healthcare, nutritional, and daily necessity categories.

### 4. COMMON — Collective Group Purchasing
- **Pooled Buying Power:** Enables groups of consumers or small businesses to aggregate purchase volume to unlock tiered vendor discounts.
- **Quorum Thresholds:** Enforces minimum participant thresholds and pooled capital reserves before initiating bulk orders.
- **Pro-Rata Participant Allocation:** Cryptographically binds individual contributions to the collective order with automated release upon target expiry.

---

## 3. The Universal Trust Engine & Security Invariants

The Universal Trust Engine enforces 10 deterministic security invariants that no AI output can bypass:

| Invariant | Description | Enforcement Mechanism |
|---|---|---|
| **INV-1: AI Cannot Approve Payments** | LLMs cannot execute or approve payments. | Policy evaluator and payment gateway invocations are isolated, deterministic backend functions. |
| **INV-2: AI Cannot Expand Permissions** | Budgets and policies cannot be modified by AI. | Consent policies require authenticated user credentials with validated JWT authorization. |
| **INV-3: Guardian Authority Limits** | Guardians cannot exceed delegated amounts. | Guardian approval ceilings and category scopes are enforced at evaluation time. |
| **INV-4: Revocation Fail-Closed** | Revoked policies or approvals immediately halt checkout. | Pre-payment verification inspects real-time revocation flags before any gateway call. |
| **INV-5: Strict Expiration TTL** | Approvals carry bounded expiration timestamps. | Expired approvals are rejected; fresh human authorization is required. |
| **INV-6: Cryptographic Fingerprint Binding** | Parameter changes invalidate prior approval. | Canonical SHA-256 fingerprint over user, merchant, items, amounts, and currency must match recorded hash. |
| **INV-7: Gateway Verification** | Frontend claims do not confirm payments. | Backend independently verifies order status with PayPal Orders API v2 before state transitions. |
| **INV-8: Idempotency & Duplicate Prevention** | No duplicate orders or captures. | Enforces unique idempotency keys and transactional database locks. |
| **INV-9: Budget Ceilings Are Not Blanket Consent** | Sufficient monthly budget does not waive rule checks. | Every order must satisfy category, merchant, and per-order limits. |
| **INV-10: No Silent Emergency Bypass** | Urgent alerts never trigger unauthorized spending. | Critical alerts notify authorized guardians but never bypass deterministic policy checks. |

---

## 4. Technology Stack

- **Backend:**
  - Python 3.11+
  - FastAPI (REST API with OpenAPI documentation)
  - SQLAlchemy 2.0 (Async ORM)
  - Pydantic v2 (Strict request/response schema validation)
  - SQLite with aiosqlite (Embedded database with migration support)
  - PyJWT & Passlib (Authentication & token verification)
  - pytest & pytest-asyncio (Comprehensive test suite)
- **Frontend:**
  - React 18
  - TypeScript
  - Vite (Build tool & development server)
  - Tailwind CSS & Custom Design System (Fiery Orange `#F04400`, Sunshine Yellow `#FFD91A`, Neon Lime `#D0F030`, Jet Black `#000000`)
  - Lucide React (Icons)
  - Recharts & Framer Motion (Data visualization & micro-interactions)
- **External Integrations:**
  - PayPal Developer Sandbox (REST API v2 Orders & Captures)
  - Google Gemini API (Structured commerce synthesis & intent extraction)

---

## 5. Getting Started & Installation

### Prerequisites
- Python 3.11 or higher
- Node.js 18 or higher (LTS recommended)
- npm or pnpm
- Git

### 1. Clone the Repository
```bash
git clone https://github.com/sainaathr/MERIDIAN-Smarter-Commerce.-Safer-Decisions.-Zero-Blind-Trust..git
cd MERIDIAN-Smarter-Commerce.-Safer-Decisions.-Zero-Blind-Trust.
```

### 2. Environment Configuration
Copy the provided environment template:
```bash
cp .env.example .env
```
Open `.env` and configure your keys:
- `SECRET_KEY`: A secure 32+ character random secret for JWT signing.
- `GEMINI_API_KEY`: Obtain from [Google AI Studio](https://aistudio.google.com/) (optional for offline testing with built-in mock fallback).
- `PAYPAL_CLIENT_ID` & `PAYPAL_CLIENT_SECRET`: Obtain from [PayPal Developer Sandbox](https://developer.paypal.com/dashboard/applications/sandbox) (optional for mock checkout simulation).

### 3. Backend Setup
```bash
# Navigate to backend and create virtual environment
python -m venv backend/.venv

# Activate virtual environment
# Windows (PowerShell):
.\backend\.venv\Scripts\Activate.ps1
# macOS/Linux:
source backend/.venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt
```

### 4. Frontend Setup
```bash
# Navigate to frontend and install packages
cd frontend
npm install
cd ..
```

---

## 6. Running the Application

Open two terminal windows:

### Terminal 1 — Backend API
```bash
# Ensure virtual environment is active
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```
*API server runs at `http://127.0.0.1:8000` (Interactive API docs at `http://127.0.0.1:8000/docs`).*

### Terminal 2 — Frontend Application
```bash
cd frontend
npm run dev
```
*Web dashboard runs at `http://localhost:5173`.*

### Demo Credentials
The initial database seeds sample personas for evaluation:
- **Alex Morgan (Personal & Business Owner):** `alex.meridian` / `Password123!`
- **Sarah Chen (Family Guardian):** `sarah.meridian` / `Password123!`
- **Eleanor Vance (Care Recipient):** `eleanor.meridian` / `Password123!`
- *Alternatively, click the Demo Persona Selector on the login screen.*

---

## 7. Running Tests

### Backend Test Suite
```bash
# Run full automated test suite
pytest backend/tests -v
```

### Frontend Build & Integrity Verification
```bash
# Run frontend test suite
node --test frontend/tests/fallback_integrity.test.mjs

# Verify TypeScript compilation and production build
npm --prefix frontend run build
```

---

## 8. PayPal Sandbox Setup & Scope Boundaries

### Sandbox Configuration
MERIDIAN operates strictly in **PayPal Developer Sandbox Mode**:
- `PAYPAL_MODE=sandbox` is strictly validated at application startup; attempting to run in production mode raises a configuration validation error.
- All payment transactions utilize simulated sandbox buyer accounts.
- When PayPal Sandbox credentials are not provided in `.env`, the system automatically falls back to an offline simulated sandbox adapter for safe local testing.

### Known Limitations & Project Scope
- **Demonstration Scope:** This project is built as an architectural prototype of safe autonomous commerce. It does not transfer real funds.
- **Medical Disclaimer:** The CARE module uses synthetic care plans and mock healthcare supplies. It is not an FDA-approved medical device or Electronic Health Record (EHR) system.
- **Banking Escrow:** Group purchasing in COMMON demonstrates cryptographic authorization and threshold coordination; it does not claim multi-jurisdictional banking escrow licensing.

---

## 9. Documentation Directory

Detailed specifications are available in the [`docs/`](./docs) directory:
- [`ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — System layer architecture, data flow, and components.
- [`SECURITY.md`](./docs/SECURITY.md) — Invariants, threat model, and prompt-injection defense.
- [`API.md`](./docs/API.md) — REST endpoints, payloads, and state transitions.
- [`DATABASE.md`](./docs/DATABASE.md) — SQLAlchemy schema and relationship entity diagrams.
- [`ORDER_CREATION_RECOVERY.md`](./docs/ORDER_CREATION_RECOVERY.md) — Two-phase payment commit and recovery mechanisms.
- [`KNOWN_LIMITATIONS.md`](./docs/KNOWN_LIMITATIONS.md) — Scope boundaries and integration details.
- [`TESTING.md`](./docs/TESTING.md) — Testing strategy and test execution guide.

---

## 10. License

This project is licensed under the MIT License — see the [LICENSE](./LICENSE) file for details.
