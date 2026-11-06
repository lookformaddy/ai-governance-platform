"""Lifecycle gate tests."""
import pytest

from agov import lifecycle
from agov.approvals import Approval, ApprovalState
from agov.models import (
    AutonomyLevel,
    DataSensitivity,
    Evidence,
    Finding,
    FindingSeverity,
    ModelRegistration,
    ModelState,
)
from agov.risk import assign_risk_tier


def _model(**kw):
    base = dict(model_id="m1", name="M", version="1.0.0", owner="a.chen",
                purpose="p", use_case="document summarization",
                use_case_sensitivity=1, data_sensitivity=DataSensitivity.PUBLIC,
                autonomy=AutonomyLevel.HUMAN_IN_LOOP, user_impact=1, reversibility=0)
    base.update(kw)
    m = ModelRegistration(**base)
    assign_risk_tier(m)
    return m


def _approved_approval():
    return Approval(approval_id="appr-1", model_id="m1", target_state="deployed",
                    submitted_by="a.chen", state=ApprovalState.APPROVED)


def _blocking_finding():
    return Finding(check_id="high_risk_has_drift_evidence", policy_key="p@v1",
                   model_id="m1", passed=False, severity=FindingSeverity.BLOCKING,
                   message="no drift evidence")


def test_validate_requires_tier():
    m = _model()
    m.risk_tier = None
    with pytest.raises(ValueError, match="risk tier"):
        lifecycle.transition(m, ModelState.VALIDATED)


def test_full_happy_path():
    m = _model()
    lifecycle.transition(m, ModelState.VALIDATED)
    lifecycle.transition(m, ModelState.APPROVED, approvals=[_approved_approval()])
    lifecycle.transition(m, ModelState.DEPLOYED, approvals=[_approved_approval()])
    lifecycle.transition(m, ModelState.MONITORING)
    assert m.state is ModelState.MONITORING


def test_approved_gate_requires_approval():
    m = _model()
    lifecycle.transition(m, ModelState.VALIDATED)
    with pytest.raises(ValueError, match="no approved deployment approval"):
        lifecycle.transition(m, ModelState.APPROVED, approvals=[])


def test_deploy_blocked_by_blocking_finding():
    m = _model()
    lifecycle.transition(m, ModelState.VALIDATED)
    lifecycle.transition(m, ModelState.APPROVED, approvals=[_approved_approval()])
    with pytest.raises(ValueError, match="blocking findings"):
        lifecycle.transition(m, ModelState.DEPLOYED, approvals=[_approved_approval()],
                             findings=[_blocking_finding()])


def test_skipping_states_rejected():
    m = _model()
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        lifecycle.transition(m, ModelState.DEPLOYED)


def test_retire_from_any_live_state():
    for state in (ModelState.REGISTERED, ModelState.VALIDATED, ModelState.MONITORING):
        m = _model()
        m.state = state
        lifecycle.transition(m, ModelState.RETIRED)
        assert m.state is ModelState.RETIRED
    m = _model()
    m.state = ModelState.RETIRED
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        lifecycle.transition(m, ModelState.VALIDATED)
