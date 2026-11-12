# MERIDIAN — Known Limitations & Scope Boundaries

## 1. Prototype Scope Boundaries
- **PayPal Sandbox Only**: All payments are executed strictly against the official PayPal Developer Sandbox (`https://api-m.sandbox.paypal.com`). No real money transfers or production credentials are used.
- **Synthesized Medical Data in Care Module**: The Care module uses synthetic mock prescriptions and essentials. It does not interface with actual EHR/EMR medical record systems or dispense medical diagnoses.
- **Collective Split Payments**: Multi-party group purchases model commitments and split payments clearly, with real sandbox capture on the organizer's checkout, rather than claiming automated multi-party banking escrow.
- **Business Cashflow Analysis**: Projections and invoice due dates are computed from transparent, verifiable sample business datasets rather than live banking webhooks.
