"""Policy registry and compliance checks.

Policies are versioned and immutable once published; a change ships as
a new version. Checks are small pure functions registered by id, so a
new control is a new function plus a policy version — no changes to the
engine. A check receives the model, its evidence list, and a context
dict (e.g. today's date) and returns (passed, message).

Severity rule: a failed check is BLOCKING when its policy marks it so
for the model's tier; otherwise it is a WARNING finding. Blocking
failures gate deployment; warnings do not, but they show up in the
compliance report until cleared.
"""
from __future__ import annotations

from datetime import date

from agov.models import (
    Evidence,
    Finding,
    FindingSeverity,
    ModelRegistration,
    Policy,
    RiskTier,
)

EVIDENCE_FRESHNESS_DAYS = 30


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def check_owner_assigned(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    return (bool(model.owner.strip()), "model must have a named owner")


def check_purpose_declared(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    return (bool(model.purpose.strip()), "model must declare its purpose")


def check_risk_tier_assigned(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    return (model.risk_tier is not None, "risk tier must be assigned before review")


def check_high_risk_has_drift_evidence(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    if model.risk_tier is not RiskTier.HIGH:
        return True, "not a high-risk model; drift evidence optional"
    drift = [e for e in evidence if e.kind == "drift" and e.passed]
    return (bool(drift), "high-risk models need passing drift evidence")


def check_high_risk_has_fairness_evidence(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    if model.risk_tier is not RiskTier.HIGH:
        return True, "not a high-risk model; fairness evidence optional"
    fair = [e for e in evidence if e.kind == "fairness" and e.passed]
    return (bool(fair), "high-risk models need passing fairness evidence")


def check_evidence_fresh(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    today = _parse_day(ctx.get("today", "")) or date.today()
    stale = []
    for e in evidence:
        measured = _parse_day(e.measured_at)
        if measured is None or (today - measured).days > EVIDENCE_FRESHNESS_DAYS:
            stale.append(e.evidence_id)
    if stale:
        return False, f"stale evidence older than {EVIDENCE_FRESHNESS_DAYS}d: {', '.join(stale)}"
    return True, "all evidence within freshness window"


def check_autonomous_has_oversight(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    from agov.models import AutonomyLevel

    if model.autonomy is not AutonomyLevel.AUTONOMOUS:
        return True, "not autonomous; human oversight inherent"
    oversight = ctx.get("human_oversight_documented", False)
    return (bool(oversight), "autonomous models need documented human oversight")


def check_review_current(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    from agov.risk import is_review_overdue

    today = ctx.get("today", "")
    if not today:
        return True, "no reference date supplied; review check skipped"
    overdue = is_review_overdue(model, today)
    return (not overdue, "periodic review is overdue" if overdue else "review is current")


def check_unacceptable_blocked(model: ModelRegistration, evidence, ctx) -> tuple[bool, str]:
    blocked = model.risk_tier is RiskTier.UNACCEPTABLE
    return (not blocked, "unacceptable-risk model: deployment prohibited" if blocked else "tier permits deployment")


CHECK_REGISTRY = {
    "owner_assigned": check_owner_assigned,
    "purpose_declared": check_purpose_declared,
    "risk_tier_assigned": check_risk_tier_assigned,
    "high_risk_has_drift_evidence": check_high_risk_has_drift_evidence,
    "high_risk_has_fairness_evidence": check_high_risk_has_fairness_evidence,
    "evidence_fresh": check_evidence_fresh,
    "autonomous_has_oversight": check_autonomous_has_oversight,
    "review_current": check_review_current,
    "unacceptable_blocked": check_unacceptable_blocked,
}

# Checks whose failure blocks deployment (for any tier they apply to).
BLOCKING_CHECKS = {
    "risk_tier_assigned",
    "high_risk_has_drift_evidence",
    "high_risk_has_fairness_evidence",
    "unacceptable_blocked",
}


class PolicyRegistry:
    """Holds versioned policies; only the latest version of each policy
    is active for new checks, older versions stay queryable."""

    def __init__(self) -> None:
        self._policies: dict[str, list[Policy]] = {}

    def publish(self, policy: Policy) -> Policy:
        versions = self._policies.setdefault(policy.policy_id, [])
        if any(p.version == policy.version for p in versions):
            raise ValueError(f"{policy.key} already published")
        if versions:
            latest = max(versions, key=lambda p: p.version)
            if policy.version <= latest.version:
                raise ValueError("new version must be greater than the latest")
            latest.superseded_by = policy.version
        for check_id in policy.check_ids:
            if check_id not in CHECK_REGISTRY:
                raise ValueError(f"unknown check id: {check_id}")
        versions.append(policy)
        return policy

    def latest(self, policy_id: str) -> Policy | None:
        versions = self._policies.get(policy_id)
        return max(versions, key=lambda p: p.version) if versions else None

    def active_policies(self, tier: RiskTier) -> list[Policy]:
        out = []
        for versions in self._policies.values():
            latest = max(versions, key=lambda p: p.version)
            if tier in latest.applies_to_tiers:
                out.append(latest)
        return sorted(out, key=lambda p: p.policy_id)

    def run_checks(self, model: ModelRegistration, evidence: list[Evidence],
                   ctx: dict | None = None, checked_at: str = "") -> list[Finding]:
        """Run every check of every active policy for the model's tier."""
        ctx = ctx or {}
        tier = model.risk_tier or RiskTier.MINIMAL
        findings: list[Finding] = []
        for policy in self.active_policies(tier):
            for check_id in policy.check_ids:
                passed, message = CHECK_REGISTRY[check_id](model, evidence, ctx)
                severity = (
                    FindingSeverity.BLOCKING
                    if (not passed and check_id in BLOCKING_CHECKS)
                    else FindingSeverity.WARNING if not passed else FindingSeverity.INFO
                )
                findings.append(Finding(
                    check_id=check_id, policy_key=policy.key, model_id=model.model_id,
                    passed=passed, severity=severity, message=message,
                    checked_at=checked_at,
                ))
        return findings


def default_registry() -> PolicyRegistry:
    """The baseline policy pack shipped with the platform."""
    registry = PolicyRegistry()
    all_tiers = [RiskTier.MINIMAL, RiskTier.LIMITED, RiskTier.HIGH, RiskTier.UNACCEPTABLE]
    registry.publish(Policy(
        policy_id="gov-basics", name="Governance Basics", version=1,
        description="Every registered model needs an owner, a declared purpose, and an assigned risk tier.",
        check_ids=["owner_assigned", "purpose_declared", "risk_tier_assigned"],
        applies_to_tiers=all_tiers,
    ))
    registry.publish(Policy(
        policy_id="high-risk-evidence", name="High-Risk Evidence", version=1,
        description="High-risk models need passing drift and fairness evidence, kept fresh.",
        check_ids=["high_risk_has_drift_evidence", "high_risk_has_fairness_evidence", "evidence_fresh"],
        applies_to_tiers=[RiskTier.HIGH],
    ))
    registry.publish(Policy(
        policy_id="oversight", name="Human Oversight", version=1,
        description="Autonomous models need documented human oversight; unacceptable-risk models are blocked.",
        check_ids=["autonomous_has_oversight", "unacceptable_blocked"],
        applies_to_tiers=all_tiers,
    ))
    registry.publish(Policy(
        policy_id="review-cadence", name="Periodic Review", version=1,
        description="Models must be re-reviewed within their tier's cadence.",
        check_ids=["review_current"],
        applies_to_tiers=[RiskTier.LIMITED, RiskTier.HIGH],
    ))
    return registry
