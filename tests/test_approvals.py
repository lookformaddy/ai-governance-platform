"""Approval workflow tests: state machine, quorum, signatures, SoD."""
import pytest

from agov.approvals import (
    Approval,
    ApprovalState,
    record_decision,
    resolve_quorum,
    sign_decision,
    transition,
    verify_signature,
)
from agov.models import RiskTier

SECRET = "test-secret"


def _approval(submitter="a.chen"):
    return Approval(approval_id="appr-1", model_id="m1", target_state="deployed",
                    submitted_by=submitter, created_at="2026-11-06")


def test_illegal_transition_rejected():
    a = _approval()
    with pytest.raises(ValueError, match="illegal approval transition"):
        transition(a, ApprovalState.APPROVED, "a.chen")


def test_submit_then_review_flow():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    assert a.state is ApprovalState.SUBMITTED
    record_decision(a, "d.kim", "approve", SECRET, "2026-11-06")
    assert a.state is ApprovalState.IN_REVIEW


def test_submitter_cannot_approve_own_request():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    with pytest.raises(ValueError, match="separation of duties"):
        record_decision(a, "a.chen", "approve", SECRET, "2026-11-06")


def test_duplicate_decision_rejected():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    record_decision(a, "d.kim", "approve", SECRET, "2026-11-06")
    with pytest.raises(ValueError, match="already decided"):
        record_decision(a, "d.kim", "approve", SECRET, "2026-11-06")


def test_high_risk_quorum_two_approvers():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    record_decision(a, "d.kim", "approve", SECRET, "2026-11-06")
    resolve_quorum(a, RiskTier.HIGH, "2026-11-06")
    assert a.state is ApprovalState.IN_REVIEW  # one of two
    record_decision(a, "e.santos", "approve", SECRET, "2026-11-06")
    resolve_quorum(a, RiskTier.HIGH, "2026-11-06")
    assert a.state is ApprovalState.APPROVED
    assert a.decided_at == "2026-11-06"


def test_limited_quorum_one_approver():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    record_decision(a, "d.kim", "approve", SECRET, "2026-11-06")
    resolve_quorum(a, RiskTier.LIMITED, "2026-11-06")
    assert a.state is ApprovalState.APPROVED


def test_rejection_ends_review_immediately():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    record_decision(a, "d.kim", "reject", SECRET, "2026-11-06")
    assert a.state is ApprovalState.REJECTED
    with pytest.raises(ValueError):
        record_decision(a, "e.santos", "approve", SECRET, "2026-11-06")


def test_signature_binds_approval_and_decision():
    sig = sign_decision(SECRET, "appr-1", "m1", "deployed", "d.kim", "approve")
    assert verify_signature(SECRET, "appr-1", "m1", "deployed", "d.kim", "approve", sig)
    assert not verify_signature(SECRET, "appr-2", "m1", "deployed", "d.kim", "approve", sig)
    assert not verify_signature(SECRET, "appr-1", "m1", "deployed", "d.kim", "reject", sig)
    assert not verify_signature("other-secret", "appr-1", "m1", "deployed", "d.kim", "approve", sig)


def test_changes_requested_returns_to_draft():
    a = _approval()
    transition(a, ApprovalState.SUBMITTED, "a.chen")
    record_decision(a, "d.kim", "approve", SECRET, "2026-11-06")
    transition(a, ApprovalState.CHANGES_REQUESTED, "d.kim")
    assert a.state is ApprovalState.CHANGES_REQUESTED
    transition(a, ApprovalState.DRAFT, "a.chen")
    assert a.state is ApprovalState.DRAFT
