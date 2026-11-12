# MERIDIAN — Testing Strategy & Verification Plan

## 1. Test Levels
- **Unit Tests**:
  - Deterministic policy evaluator (allow, require approval, deny).
  - Transaction fingerprint stability and collision resistance.
  - Pydantic schema validation for AI inputs/outputs.
- **Security Invariant Tests**:
  - Direct execution of the 10 security invariants (e.g. tampering, expired approvals, guardian privilege escalation).
- **Integration Tests**:
  - End-to-end PayPal sandbox order creation & capture.
  - Gemini structured extraction with live/mock fallback.
  - Full end-to-end lifecycle flow: Dynamic proposal persistence -> Policy evaluation -> State transition -> Approval -> PayPal order creation -> Capture -> Live transaction history & immutable audit log reflection (`test_phase5_e2e_integration.py`).
- **Frontend Component & Route Tests**:
  - Navigation, Trust Centre inspectability, Module workflows.

---

## 2. Test Execution Commands
- **Backend Tests**:
  ```powershell
  $env:PYTHONPATH = "backend"
  .\backend\.venv\Scripts\pytest backend\tests -v
  ```
- **Frontend Type & Build Verification**:
  ```powershell
  cd frontend
  npm run build
  ```
