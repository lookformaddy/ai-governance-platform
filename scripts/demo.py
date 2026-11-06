"""End-to-end demo: register models, assign risk tiers, run policy
checks, attach drift/fairness evidence, approve a high-risk deployment
with a two-approver quorum, deploy behind the lifecycle gates, and
print the compliance report.

All data is synthetic; all providers are deterministic. Run with:
    PYTHONPATH=src python scripts/demo.py
"""
from __future__ import annotations

import json

from agov import approvals as approvals_mod
from agov import lifecycle as lifecycle_mod
from agov.audit import AuditLog
from agov.evidence import attach_evidence
from agov.models import (
    Approval,
    ApprovalState,
    AutonomyLevel,
    DataSensitivity,
    Evidence,
    ModelRegistration,
    ModelState,
)
from agov.policies import default_registry
from agov.reporting import compliance_report
from agov.risk import assign_risk_tier

TODAY = "2026-11-06"
SECRET = "demo-signing-secret"


def _model(**kw) -> ModelRegistration:
    return ModelRegistration(version="1.0.0", registered_at=TODAY, **kw)


def main() -> None:
    audit = AuditLog()
    registry = default_registry()
    evidence_store: dict[str, list[Evidence]] = {}
    findings_store: dict[str, list] = {}

    # 1. Register three synthetic models across the risk spectrum.
    high = _model(model_id="eligibility-scorer", name="Eligibility Scorer",
                  owner="a.chen", purpose="Score applications for eligibility review",
                  use_case="eligibility scoring", use_case_sensitivity=9,
                  data_sensitivity=DataSensitivity.REGULATED,
                  autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9, reversibility=8)
    limited = _model(model_id="support-triage", name="Support Triage",
                     owner="b.okafor", purpose="Route support tickets to the right queue",
                     use_case="support ticket triage", use_case_sensitivity=4,
                     data_sensitivity=DataSensitivity.INTERNAL,
                     autonomy=AutonomyLevel.HUMAN_IN_LOOP, user_impact=4, reversibility=2)
    minimal = _model(model_id="doc-summarizer", name="Doc Summarizer",
                     owner="c.patel", purpose="Summarize public help articles",
                     use_case="document summarization", use_case_sensitivity=1,
                     data_sensitivity=DataSensitivity.PUBLIC,
                     autonomy=AutonomyLevel.HUMAN_IN_LOOP, user_impact=1, reversibility=0)
    models = [high, limited, minimal]
    for m in models:
        assign_risk_tier(m)
        m.last_reviewed_at = TODAY
        audit.append(TODAY, m.owner, "model_registered", m.model_id,
                     f"tier={m.risk_tier.value} score={m.risk_score}")
        print(f"registered {m.model_id}: tier={m.risk_tier.value} score={m.risk_score}")

    # 2. Attach drift + fairness evidence to the high-risk model.
    for ev in (
        Evidence("ev-drift-1", "eligibility-scorer", "drift", "psi", 0.08, 0.20,
                 True, TODAY, "population stability index, monthly batch"),
        Evidence("ev-fair-1", "eligibility-scorer", "fairness", "demographic_parity_gap",
                 0.03, 0.10, True, TODAY, "max gap across protected groups"),
    ):
        attach_evidence(evidence_store, ev)
        print(f"attached {ev.kind} evidence {ev.evidence_id} (passed={ev.passed})")

    # 3. Run policy checks for every model.
    for m in models:
        findings = registry.run_checks(
            m, evidence_store.get(m.model_id, []),
            ctx={"today": TODAY, "human_oversight_documented": True}, checked_at=TODAY)
        findings_store[m.model_id] = findings
        passed = sum(1 for f in findings if f.passed)
        print(f"checks {m.model_id}: {passed}/{len(findings)} passed; "
              f"blocking failures: {sum(1 for f in findings if not f.passed and f.severity.value == 'blocking')}")
        audit.append(TODAY, "governance", "checks_run", m.model_id, f"{passed}/{len(findings)}")

    # 4. Approval workflow for the high-risk deployment: submit, then
    #    two distinct approvers (quorum = 2). Submitter cannot approve.
    approval = Approval(approval_id="appr-001", model_id=high.model_id,
                        target_state="deployed", submitted_by="a.chen",
                        reason="First production deployment", created_at=TODAY)
    approvals_mod.transition(approval, ApprovalState.SUBMITTED, "a.chen")
    try:
        approvals_mod.record_decision(approval, "a.chen", "approve", SECRET, TODAY)
    except ValueError as exc:
        print(f"separation of duties enforced: {exc}")
    for approver in ("d.kim", "e.santos"):
        approvals_mod.record_decision(approval, approver, "approve", SECRET, TODAY)
        approvals_mod.resolve_quorum(approval, high.risk_tier, TODAY)
        print(f"decision by {approver}: state={approval.state.value}")
    assert approval.state is ApprovalState.APPROVED
    audit.append(TODAY, "d.kim", "approval_approved", high.model_id, "appr-001")

    # 5. Lifecycle gates: validated -> approved -> deployed -> monitoring.
    for state in (ModelState.VALIDATED, ModelState.APPROVED, ModelState.DEPLOYED,
                  ModelState.MONITORING):
        lifecycle_mod.transition(high, state, approvals=[approval],
                                 findings=findings_store[high.model_id])
        print(f"lifecycle {high.model_id}: -> {state.value}")
        audit.append(TODAY, "governance", "lifecycle_transition", high.model_id, state.value)

    # 6. Compliance report + audit verification.
    report = compliance_report(models, findings_store, [approval], TODAY)
    print("\ncompliance report:")
    print(json.dumps(report, indent=2))
    ok, message = audit.verify()
    print(f"\naudit: valid={ok} ({message})")
    assert ok and report["coverage_pct"] == 100.0 and report["blocking_findings"] == 0
    print("\ndemo OK: high-risk model deployed with signed quorum approval, "
          "100% check coverage, 0 blocking findings, audit chain intact.")


if __name__ == "__main__":
    main()
