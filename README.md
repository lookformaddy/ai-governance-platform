# AI Governance Platform

An **AI governance control plane** for the enterprise: one place where
every AI model is registered, risk-tiered, checked against versioned
policies, approved for deployment by the right humans, and reported on
for compliance — with a tamper-evident audit trail behind all of it.

Most organizations can tell you which models they *ship*. Few can
answer, for any given model, "what risk tier is it, which policies were
checked, who approved it, and is its evidence still fresh?" That gap
is what regulators audit and what incidents exploit. This platform
closes it with deterministic, explainable machinery: no black-box
scoring, no network calls, no real LLM dependency.

## What it does

- **Model inventory & registration** — every model version registered
  with owner, purpose, use case, data sensitivity, autonomy, and
  impact; duplicate registrations rejected, invalid declarations
  rejected with reasons.
- **Risk-tiered governance** — deterministic scoring (use-case
  sensitivity, data sensitivity, autonomy, user impact) maps each
  model to `minimal / limited / high / unacceptable`. Prohibited use
  cases (e.g. social scoring) short-circuit to `unacceptable`
  regardless of score. Each tier carries its own review cadence
  (365 / 180 / 90 days) and approval quorum (0 / 1 / 2 approvers).
- **Policy registry** — versioned, immutable-once-published policies.
  A change ships as a new version; old check results stay
  interpretable. Checks are small pure functions registered by id, so
  a new control is a new function plus a policy version.
- **Compliance checks** — owner/purpose/tier presence, drift and
  fairness evidence for high-risk models, evidence freshness (30 days),
  documented human oversight for autonomous models, review currency,
  and the unacceptable-risk deployment block. Failures are `blocking`
  (gate deployment) or `warning` (reported until cleared).
- **Approval workflow** — `draft → submitted → in_review → approved /
  rejected`, with `changes_requested` returning to draft. Decisions
  are SHA-256 signed over the approval, model, and decision, so a
  signature cannot be replayed onto another approval. Separation of
  duties is enforced (the submitter can never approve their own
  request) and a single rejection ends the review.
- **Lifecycle gates** — `registered → validated → approved → deployed →
  monitoring → retired`. Each step is gated on governance state, not
  on a human remembering to check: validation needs a tier, approval
  needs an approved approval, deployment needs zero blocking findings
  *and* an approved approval. Gate failures raise with the reason.
- **Drift / fairness evidence** — measurements, not claims: metric,
  threshold, pass/fail, and measurement date. A record whose `passed`
  flag contradicts its metric vs threshold is rejected at attach time;
  stale evidence fails the freshness check at decision time.
- **Audit trail** — SHA-256 hash-chained, append-only; `verify()`
  replays the chain and reports the first broken link (tamper,
  reorder, or deletion).
- **Compliance reporting** — coverage by tier, open and blocking
  findings, overdue reviews (fail-closed: no tier or no review date
  counts as overdue), and the approval pipeline.
- **FastAPI service + CLI**, deterministic synthetic demo (seeded; no
  network calls, no API keys).

## Architecture

See [docs/architecture.md](docs/architecture.md).

```
register ──▶ risk scoring (deterministic, explainable)
                 │ tier + cadence + quorum
                 ▼
evidence attach (validated measurements) ──▶ policy checks (versioned policies)
                 │                                        │ findings
                 ▼                                        ▼
approval workflow (signed decisions, SoD, quorum) ──▶ lifecycle gates
                 │                                        │
                 ▼                                        ▼
        compliance report (coverage / findings / overdue) + hash-chained audit log
```

## How to run

```bash
pip install -r requirements.txt
make test          # pytest suite (core tests run on stdlib only)
make demo          # end-to-end demo: register → tier → checks → approve → deploy → report
uvicorn agov.service:app --host 0.0.0.0 --port 8000   # with PYTHONPATH=src
```

CLI:

```bash
PYTHONPATH=src python -m agov.cli register --model-id eligibility-scorer \
  --name "Eligibility Scorer" --owner a.chen \
  --purpose "Score applications for eligibility review" \
  --use-case "eligibility scoring" --use-case-sensitivity 9 \
  --data-sensitivity regulated --autonomy human_on_loop \
  --user-impact 9 --reversibility 8
```

## API surface

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| `GET` | `/health` | Liveness + model count |
| `POST` | `/models` | Register a model (tier assigned on write) |
| `GET` | `/models`, `/models/{id}` | Inventory |
| `POST` | `/models/{id}/evidence` | Attach validated drift/fairness evidence |
| `POST` | `/models/{id}/checks` | Run the tier's active policies |
| `POST` | `/models/{id}/approvals` | Create a deployment approval |
| `POST` | `/approvals/{id}/submit`, `/approvals/{id}/decide` | Workflow + signed decisions |
| `POST` | `/models/{id}/lifecycle` | Gated lifecycle transition |
| `GET` | `/compliance/report` | Coverage, findings, overdue reviews |
| `GET` | `/audit/verify` | Hash-chain verification |

## Key design decisions and trade-offs

- **Deterministic scoring over ML-based risk models.** A governance
  decision must be explainable to an auditor; a weighted sum with a
  written rationale is. The trade-off is coarseness — scores are
  ordinal, not probabilistic — which is the right trade for a control
  plane.
- **Fail-closed everywhere.** No tier or no review date counts as
  overdue; missing evidence blocks high-risk deployment; a rejected
  approval cannot be re-decided. A governance system that fails open
  is worse than no system, because it manufactures false assurance.
- **Policies versioned, checks registered.** Governance rules change;
  the record of what was checked must not. Immutability plus a
  function registry keeps the engine stable while controls evolve.
- **Signatures, not just names.** An approval decision is a hash over
  the approval, model, target state, approver, and decision, so a
  decision cannot be copied to a different approval. This is
  tamper-evidence, not PKI — a production deployment would use real
  key-based signatures and a key-management service.
- **Pure-stdlib core, FastAPI at the edge.** All decision logic is
  testable without a server, a database, or a network. The service is
  a thin wrapper, so the API can never become a second source of
  truth. The trade-off is in-memory stores: persistence (a real
  database, plus durable audit storage) is the explicit next step.
- **Blocking vs warning findings.** Not every failed check should stop
  a deployment; distinguishing them keeps the gate credible instead of
  a rubber stamp people route around.

## What it demonstrates

Risk-tiered AI governance end to end: explainable scoring, versioned
policy-as-code, signed multi-approver workflows with separation of
duties, state-machine lifecycle gates, evidence lifecycle management,
hash-chained audit, and compliance reporting — built as a typed,
tested, enterprise-shaped Python codebase with a FastAPI service, CLI,
Docker image, and CI definition.
