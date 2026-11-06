"""Compliance reporting.

Aggregates the governance record into the report a compliance officer
actually asks for: how much of the inventory is covered by current
checks, broken down by risk tier; which findings are still open; which
models are overdue for review; and the approval pipeline. Every number
is computed from the same in-memory stores the API serves, so the
report can never drift from the live record.
"""
from __future__ import annotations

from agov.models import Approval, Finding, FindingSeverity, ModelRegistration, RiskTier
from agov.risk import is_review_overdue


def compliance_report(models: list[ModelRegistration],
                      findings_by_model: dict[str, list[Finding]],
                      approvals: list[Approval],
                      today: str) -> dict:
    total = len(models)
    checked = sum(1 for m in models if m.model_id in findings_by_model)
    open_findings = [
        f for findings in findings_by_model.values() for f in findings if not f.passed
    ]
    blocking = [f for f in open_findings if f.severity is FindingSeverity.BLOCKING]
    overdue = [m for m in models if is_review_overdue(m, today)]

    by_tier: dict[str, dict] = {}
    for tier in RiskTier:
        tier_models = [m for m in models if m.risk_tier is tier]
        tier_checked = sum(1 for m in tier_models if m.model_id in findings_by_model)
        by_tier[tier.value] = {
            "models": len(tier_models),
            "checked": tier_checked,
            "coverage_pct": round(100.0 * tier_checked / len(tier_models), 1) if tier_models else 100.0,
            "open_findings": sum(
                1 for m in tier_models
                for f in findings_by_model.get(m.model_id, []) if not f.passed
            ),
        }

    approval_states: dict[str, int] = {}
    for a in approvals:
        approval_states[a.state.value] = approval_states.get(a.state.value, 0) + 1

    return {
        "generated_at": today,
        "total_models": total,
        "models_checked": checked,
        "coverage_pct": round(100.0 * checked / total, 1) if total else 100.0,
        "open_findings": len(open_findings),
        "blocking_findings": len(blocking),
        "overdue_reviews": len(overdue),
        "overdue_model_ids": sorted(m.model_id for m in overdue),
        "by_tier": by_tier,
        "approvals_by_state": approval_states,
    }
