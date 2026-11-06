"""Deployment approval workflow.

State machine: draft -> submitted -> in_review -> approved | rejected,
with changes_requested sending the request back to draft. Decisions are
signed: each approver's decision is hashed together with the approval
id, the model, the target state, and a signing secret, so a decision
cannot be replayed onto a different approval. Separation of duties is
enforced — the submitter can never approve their own request — and a
HIGH-risk model needs two distinct approvers (quorum from the tier).
A single rejection ends the review; approval requires the full quorum
with no rejections.
"""
from __future__ import annotations

import hashlib
import json

from agov.models import Approval, ApprovalState, RiskTier

VALID_TRANSITIONS = {
    ApprovalState.DRAFT: {ApprovalState.SUBMITTED},
    ApprovalState.SUBMITTED: {ApprovalState.IN_REVIEW, ApprovalState.REJECTED},
    ApprovalState.IN_REVIEW: {
        ApprovalState.APPROVED,
        ApprovalState.REJECTED,
        ApprovalState.CHANGES_REQUESTED,
    },
    ApprovalState.CHANGES_REQUESTED: {ApprovalState.DRAFT},
    ApprovalState.APPROVED: set(),
    ApprovalState.REJECTED: set(),
}


def sign_decision(secret: str, approval_id: str, model_id: str,
                  target_state: str, approver: str, decision: str) -> str:
    payload = {
        "approval_id": approval_id,
        "model_id": model_id,
        "target_state": target_state,
        "approver": approver,
        "decision": decision,
        "secret": secret,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_signature(secret: str, approval_id: str, model_id: str,
                     target_state: str, approver: str, decision: str,
                     signature: str) -> bool:
    return sign_decision(secret, approval_id, model_id, target_state,
                         approver, decision) == signature


def transition(approval: Approval, to_state: ApprovalState, actor: str) -> Approval:
    """Move an approval to a new state. Raises ValueError on an illegal
    transition — there is no way to skip a state or move backwards."""
    allowed = VALID_TRANSITIONS.get(approval.state, set())
    if to_state not in allowed:
        raise ValueError(
            f"illegal approval transition {approval.state.value} -> {to_state.value}"
        )
    approval.state = to_state
    return approval


def record_decision(approval: Approval, approver: str, decision: str,
                    secret: str, at: str) -> Approval:
    """Record one signed approver decision. Enforces separation of
    duties and the review-state gate; resolves the approval once the
    quorum is met or any approver rejects."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    if approval.state not in (ApprovalState.SUBMITTED, ApprovalState.IN_REVIEW):
        raise ValueError(f"cannot decide on an approval in state {approval.state.value}")
    if approver == approval.submitted_by:
        raise ValueError("separation of duties: submitter cannot approve their own request")
    if any(d["approver"] == approver for d in approval.decisions):
        raise ValueError(f"{approver} has already decided on this approval")
    signature = sign_decision(secret, approval.approval_id, approval.model_id,
                              approval.target_state, approver, decision)
    approval.decisions.append({
        "approver": approver, "decision": decision,
        "signature": signature, "at": at,
    })
    if approval.state is ApprovalState.SUBMITTED:
        approval.state = ApprovalState.IN_REVIEW
    if approval.rejecter_count() >= 1:
        approval.state = ApprovalState.REJECTED
        approval.decided_at = at
    return approval


def resolve_quorum(approval: Approval, tier: RiskTier, at: str) -> Approval:
    """Mark the approval APPROVED once the tier quorum is met with no
    rejections. Call after recording decisions; a no-op otherwise."""
    if approval.state is not ApprovalState.IN_REVIEW:
        return approval
    if approval.rejecter_count() >= 1:
        return approval
    if approval.approver_count() >= tier.required_approvers:
        approval.state = ApprovalState.APPROVED
        approval.decided_at = at
    return approval


def is_approved(approval: Approval) -> bool:
    return approval.state is ApprovalState.APPROVED
