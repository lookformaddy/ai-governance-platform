# Architecture — AI Governance Platform

## Diagram (described)

```
                        ┌─────────────────────┐
                        │  FastAPI service     │
                        │  (agov/service.py)   │
                        │  thin HTTP wrapper   │
                        └──────┬──────────────┘
                               │ owns stores, stamps time,
                               │ maps ValueError -> 400
        ┌──────────────┬───────┼───────────────┬──────────────┐
        ▼              ▼       ▼               ▼              ▼
  ┌───────────┐ ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │  risk.py  │ │ policies.py│ │approvals │ │lifecycle │ │ audit.py │
  │  scoring  │ │  checks +  │ │ .py      │ │ .py      │ │ hash-    │
  │  + tiers  │ │  registry  │ │ workflow │ │  gates   │ │ chained  │
  └───────────┘ └───────────┘ └──────────┘ └──────────┘ └──────────┘
        │              │           │            │              │
        ▼              ▼           ▼            ▼              ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  models.py — dataclasses (registrations, policies, findings,  │
  │  evidence, approvals, audit entries). No I/O, no decisions.   │
  └────────────────────────────────────────────────────────────────┘
        │              │           │            │              │
        ▼              ▼           ▼            ▼              ▼
  ┌──────────────┐ ┌───────────┐ ┌───────────────────────────────┐
  │ evidence.py  │ │reporting.py│ │  cli.py / scripts/demo.py     │
  │ validation + │ │ compliance │ │  entry points over the core   │
  │ freshness    │ │ report     │ │                               │
  └──────────────┘ └───────────┘ └───────────────────────────────┘
```

All decision logic lives in the pure-stdlib core (`risk`, `policies`,
`approvals`, `lifecycle`, `evidence`, `reporting`, `audit`). The
FastAPI service and the CLI are entry points only: they own the
in-memory stores, stamp timestamps, and translate domain errors into
HTTP 400s. There is exactly one implementation of every rule, so the
API can never drift from the core.

## Module responsibilities

| Module | Responsibility |
| ------ | -------------- |
| `models.py` | Domain dataclasses and enums. `RiskTier` carries its own review cadence and approval quorum so no other module hard-codes per-tier numbers. No I/O; timestamps are explicit arguments, which keeps tests deterministic. |
| `risk.py` | Deterministic weighted scoring (max 100) and tier thresholds; prohibited-use-case override to `UNACCEPTABLE`; review-overdue check (fail-closed). |
| `policies.py` | Versioned `PolicyRegistry` (publish / latest / active-for-tier) and the check registry: small pure functions `(model, evidence, ctx) -> (passed, message)`. `run_checks` executes every check of every active policy for the model's tier and grades failures as blocking or warning. |
| `evidence.py` | Evidence validation (kind, ISO date, `passed` must agree with metric vs threshold) and freshness; invalid measurements are rejected at attach time and never enter the record. |
| `approvals.py` | Approval state machine, SHA-256 decision signatures bound to the approval, separation of duties, duplicate-decision rejection, and quorum resolution by tier. |
| `lifecycle.py` | Model state machine with gates: validation needs a tier, approval needs an approved approval, deployment needs zero blocking findings plus an approved approval. |
| `audit.py` | Append-only SHA-256 hash-chained log; `verify()` replays the chain and reports the first broken link. |
| `reporting.py` | Compliance report: coverage overall and by tier, open/blocking findings, overdue reviews, approval pipeline — all computed from the same stores the API serves. |
| `service.py` | FastAPI app; in-memory stores; request validation via Pydantic; domain `ValueError` → 400, unknown model/approval → 404, duplicate registration → 409. |
| `cli.py` | `register` (tier assignment) and `report` commands over the core. |

## Data flow: deploying a high-risk model

1. **Register** — `POST /models` validates the declaration, assigns
   the risk tier, and writes an audit entry.
2. **Attach evidence** — drift and fairness measurements are validated
   on attach (metric vs threshold must agree with `passed`).
3. **Run checks** — the tier's active policies run; findings are
   stored and the model's review date is stamped.
4. **Approval** — created in `draft`, submitted, then decided by two
   distinct approvers (high-risk quorum). Each decision is signed;
   the submitter's own decision is rejected.
5. **Lifecycle** — `validated → approved → deployed → monitoring`,
   each step re-checking its gate against the live approval and
   finding stores.
6. **Report** — `/compliance/report` aggregates coverage, findings,
   and overdue reviews; `/audit/verify` proves the chain.

## Invariants

- A model cannot be validated without a risk tier.
- A model cannot be deployed with a blocking finding or without an
  approved approval.
- An approval decision cannot be replayed onto another approval
  (signature binds approval id, model, target state, approver, decision).
- The submitter can never approve their own request; one rejection
  ends the review.
- Evidence whose `passed` flag contradicts its metric never enters
  the store; stale evidence fails the freshness check at decision time.
- A model with no tier or no review date is overdue, never current.
- The audit chain is append-only; any edit, reorder, or deletion is
  detected by `verify()`.

## Trade-offs and next steps

- **In-memory stores.** The service keeps state in process memory so
  the core stays dependency-free and testable. A production deployment
  backs the stores with a database and persists the audit log to
  write-once storage.
- **Symmetric signatures, not PKI.** Decision signatures use a shared
  secret (HMAC-style SHA-256). Real deployments should use per-approver
  keys and a key-management service.
- **Synchronous checks.** Policy checks run inline in the request.
  Checks that need external evidence (e.g. a drift-monitoring system)
  would move to an async job with a pending-findings state.
- **No authentication/authorization.** The service assumes a trusted
  network; identity and role checks (who may approve which tier)
  belong in front of it.
