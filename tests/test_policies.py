"""Policy registry and compliance-check tests."""
import pytest

from agov.models import (
    AutonomyLevel,
    DataSensitivity,
    Evidence,
    FindingSeverity,
    ModelRegistration,
    Policy,
    RiskTier,
)
from agov.policies import CHECK_REGISTRY, default_registry
from agov.risk import assign_risk_tier


def _model(tier_kwargs=None, **kw):
    base = dict(model_id="m1", name="M", version="1.0.0", owner="a.chen",
                purpose="p", use_case="document summarization",
                use_case_sensitivity=1, data_sensitivity=DataSensitivity.PUBLIC,
                autonomy=AutonomyLevel.HUMAN_IN_LOOP, user_impact=1, reversibility=0)
    base.update(kw)
    m = ModelRegistration(**base)
    assign_risk_tier(m)
    return m


def test_default_registry_has_four_policies():
    reg = default_registry()
    assert reg.latest("gov-basics") is not None
    assert reg.latest("high-risk-evidence") is not None
    assert reg.latest("oversight") is not None
    assert reg.latest("review-cadence") is not None


def test_policy_versioning_supersedes_old():
    reg = default_registry()
    old = reg.latest("gov-basics")
    reg.publish(Policy(policy_id="gov-basics", name="Governance Basics", version=2,
                       description="v2", check_ids=["owner_assigned"],
                       applies_to_tiers=[RiskTier.MINIMAL]))
    assert reg.latest("gov-basics").version == 2
    assert old.superseded_by == 2
    with pytest.raises(ValueError):
        reg.publish(Policy(policy_id="gov-basics", name="x", version=2,
                           description="dup", check_ids=[],
                           applies_to_tiers=[RiskTier.MINIMAL]))


def test_unknown_check_id_rejected():
    reg = default_registry()
    with pytest.raises(ValueError, match="unknown check id"):
        reg.publish(Policy(policy_id="bad", name="Bad", version=1, description="",
                           check_ids=["no_such_check"], applies_to_tiers=[RiskTier.MINIMAL]))


def test_minimal_model_passes_basics():
    reg = default_registry()
    m = _model()
    findings = reg.run_checks(m, [], ctx={"today": "2026-11-06"}, checked_at="2026-11-06")
    assert findings and all(f.passed for f in findings)


def test_high_risk_without_evidence_has_blocking_findings():
    reg = default_registry()
    m = _model(use_case="eligibility scoring", use_case_sensitivity=9,
               data_sensitivity=DataSensitivity.REGULATED,
               autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9)
    m.last_reviewed_at = "2026-11-06"
    findings = reg.run_checks(m, [], ctx={"today": "2026-11-06"}, checked_at="2026-11-06")
    blocking = [f for f in findings if not f.passed and f.severity is FindingSeverity.BLOCKING]
    assert {f.check_id for f in blocking} == {
        "high_risk_has_drift_evidence", "high_risk_has_fairness_evidence"}


def test_high_risk_with_evidence_passes():
    reg = default_registry()
    m = _model(use_case="eligibility scoring", use_case_sensitivity=9,
               data_sensitivity=DataSensitivity.REGULATED,
               autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9)
    m.last_reviewed_at = "2026-11-06"
    evidence = [
        Evidence("e1", "m1", "drift", "psi", 0.08, 0.20, True, "2026-11-06"),
        Evidence("e2", "m1", "fairness", "demographic_parity_gap", 0.03, 0.10, True, "2026-11-06"),
    ]
    findings = reg.run_checks(m, evidence, ctx={"today": "2026-11-06"}, checked_at="2026-11-06")
    assert all(f.passed for f in findings), [f for f in findings if not f.passed]


def test_stale_evidence_fails_freshness():
    reg = default_registry()
    m = _model(use_case="eligibility scoring", use_case_sensitivity=9,
               data_sensitivity=DataSensitivity.REGULATED,
               autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9)
    m.last_reviewed_at = "2026-11-06"
    evidence = [
        Evidence("e1", "m1", "drift", "psi", 0.08, 0.20, True, "2026-09-01"),
        Evidence("e2", "m1", "fairness", "demographic_parity_gap", 0.03, 0.10, True, "2026-11-06"),
    ]
    findings = reg.run_checks(m, evidence, ctx={"today": "2026-11-06"}, checked_at="2026-11-06")
    failed = {f.check_id for f in findings if not f.passed}
    assert "evidence_fresh" in failed


def test_unacceptable_model_blocked():
    reg = default_registry()
    m = _model(use_case="social scoring")
    findings = reg.run_checks(m, [], ctx={"today": "2026-11-06"}, checked_at="2026-11-06")
    failed = [f for f in findings if f.check_id == "unacceptable_blocked"]
    assert failed and not failed[0].passed
    assert failed[0].severity is FindingSeverity.BLOCKING


def test_autonomous_without_oversight_fails():
    m = _model(autonomy=AutonomyLevel.AUTONOMOUS)
    passed, _ = CHECK_REGISTRY["autonomous_has_oversight"](m, [], {})
    assert passed is False
    passed, _ = CHECK_REGISTRY["autonomous_has_oversight"](
        m, [], {"human_oversight_documented": True})
    assert passed is True
