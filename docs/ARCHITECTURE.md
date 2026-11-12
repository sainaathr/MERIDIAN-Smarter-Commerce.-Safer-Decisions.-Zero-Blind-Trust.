# MERIDIAN — System Architecture Specification

## 1. Executive Summary
**MERIDIAN** (*"Commerce without blind trust"*) is an AI-native financial autonomy and commerce coordination platform. It introduces a fundamental separation of powers:

> **The AI proposes actions; deterministic backend policy code decides whether an action is permitted.**

No Large Language Model possesses direct authority to approve, capture, or alter financial transactions. Every transaction is governed by the **Universal Trust Engine**, using cryptographic transaction fingerprinting and explicit human consent policies.

---

## 2. Logical Architecture Layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Frontend Experience Layer                        │
│  (React 19 + TypeScript + Vite + Tailwind CSS + Framer Motion + Recharts) │
│  - Overview Dashboard      - PACT Personal Commerce                     │
│  - FLOW Business Intel     - COMMON Collective Split                    │
│  - CARE Assisted Commerce  - Trust Centre & Audit Inspector             │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ JSON / REST
┌────────────────────────────────────▼────────────────────────────────────┐
│                            API Gateway Layer                            │
│                  (FastAPI + Pydantic v2 + OAuth Sessions)               │
│  - Authentication & RBAC   - Strict Request / Response Schema Validation│
│  - CORS / Rate Limiting    - Transaction State Transitions              │
└──────────────┬─────────────────────┬─────────────────────┬──────────────┘
               │                     │                     │
┌──────────────▼──────────┐ ┌────────▼──────────┐ ┌────────▼──────────────┐
│  AI Orchestration Layer │ │   Universal Trust │ │   PayPal Sandbox       │
│  (Google Gemini API)    │ │        Engine     │ │   Payment Adapter      │
│  - Intent Extraction    │ │- SHA-256 Finger-  │ │- Server-side OAuth     │
│  - Product Summarization│ │  printing         │ │- Orders API v2 Create  │
│  - Cashflow Explanations│ │- Deterministic    │ │- Verified Capture      │
│  - Untrusted Content    │ │  Policy Engine    │ │- Duplicate Check       │
│    Firewall             │ │- Consent Lifecycle│ │- Idempotency           │
└─────────────────────────┘ └────────┬──────────┘ └───────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────┐
│                           Data & Audit Layer                            │
│                 (SQLAlchemy 2.0 + SQLite / PostgreSQL)                  │
│  - Users & Guardians       - Consent Policies & Spending Budgets        │
│  - Purchase Proposals      - Cryptographic Transaction Fingerprints     │
│  - Immutable Audit Events  - PayPal Order & Capture Records             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. The 4 Commerce Modules & 1 Universal Trust Engine

1. **PERSONAL (PACT Commerce)**:
   Personal purchasing assistant with strict price and merchant bounds. External product descriptions are treated as untrusted to defeat prompt injection attempts.
2. **BUSINESS (FLOW Intelligence)**:
   Cashflow projection, upcoming invoice calendar, and explainable purchasing recommendations.
3. **COLLECTIVE (COMMON Commerce)**:
   Shared group purchases, contribution tracking, and split transparency.
4. **CARE (Assisted Commerce)**:
   Assisted commerce for vulnerable individuals or seniors. Supports **Mode A** (per-order explicit approval) and **Mode B** (strictly bounded recurring reordering with budget, product, and quantity ceilings). Anti-emergency bypass invariant guarantees emergencies cannot silently override consent.
5. **UNIVERSAL TRUST ENGINE (Shared Backbone)**:
   A single, deterministic gatekeeper enforcing:
   - Canonical SHA-256 fingerprinting over material transaction parameters.
   - 11-stage transaction lifecycle.
   - User and guardian role-based permissions.
   - Tamper invalidation: any mutation of price, merchant, quantity, or recipient immediately resets approvals.
