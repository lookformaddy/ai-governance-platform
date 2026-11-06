"""Drift / fairness evidence attachment and validation.

Evidence is a measurement, not a claim: each record carries the metric,
its threshold, and whether it passed, plus the date it was measured.
The platform never trusts a stale or failing measurement — the policy
engine re-checks freshness and pass status at decision time, so an
approval granted on old evidence cannot silently carry a deployment.
"""
from __future__ import annotations

from datetime import date

from agov.models import Evidence

EVIDENCE_KINDS = {"drift", "fairness", "performance", "security"}


def validate_evidence(evidence: Evidence) -> list[str]:
    errors: list[str] = []
    if evidence.kind not in EVIDENCE_KINDS:
        errors.append(f"kind must be one of {sorted(EVIDENCE_KINDS)}")
    if not evidence.evidence_id.strip():
        errors.append("evidence_id is required")
    if not evidence.metric_name.strip():
        errors.append("metric_name is required")
    if not evidence.measured_at.strip():
        errors.append("measured_at is required")
    else:
        try:
            date.fromisoformat(evidence.measured_at[:10])
        except ValueError:
            errors.append("measured_at must be an ISO date")
    expected = evidence.metric_value <= evidence.threshold
    if evidence.passed != expected:
        errors.append(
            f"passed={evidence.passed} contradicts metric_value "
            f"{evidence.metric_value} vs threshold {evidence.threshold}"
        )
    return errors


def is_fresh(evidence: Evidence, today: str, max_age_days: int = 30) -> bool:
    """True when the measurement is no older than ``max_age_days``."""
    try:
        measured = date.fromisoformat(evidence.measured_at[:10])
        current = date.fromisoformat(today[:10])
    except ValueError:
        return False
    return 0 <= (current - measured).days <= max_age_days


def attach_evidence(store: dict[str, list[Evidence]], evidence: Evidence) -> Evidence:
    """Validate and attach evidence to the in-memory store. Raises
    ValueError on invalid evidence — bad measurements never enter the
    record silently."""
    errors = validate_evidence(evidence)
    if errors:
        raise ValueError("; ".join(errors))
    store.setdefault(evidence.model_id, []).append(evidence)
    return evidence
