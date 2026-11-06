"""Model lifecycle gates.

State machine: registered -> validated -> approved -> deployed ->
monitoring -> retired. Each forward step is gated on governance state,
not on a human remembering to check:

* validated: registration valid + risk tier assigned.
* approved: an APPROVED deployment approval exists for the model.
* deployed: no blocking policy findings from the latest check run.
* monitoring: deployment recorded (the model is live and observed).
* retired: allowed from any non-retired state.

A gate failure raises ValueError with the reason, so a deployment can
never slip past a missing approval or a blocking finding.
"""
from __future__ import annotations

from agov.models import Approval, Finding, FindingSeverity, ModelRegistration, ModelState

VALID_TRANSITIONS = {
    ModelState.REGISTERED: {ModelState.VALIDATED, ModelState.RETIRED},
    ModelState.VALIDATED: {ModelState.APPROVED, ModelState.RETIRED},
    ModelState.APPROVED: {ModelState.DEPLOYED, ModelState.RETIRED},
    ModelState.DEPLOYED: {ModelState.MONITORING, ModelState.RETIRED},
    ModelState.MONITORING: {ModelState.RETIRED},
    ModelState.RETIRED: set(),
}


def gate_errors(model: ModelRegistration, to_state: ModelState,
                approvals: list[Approval] | None = None,
                findings: list[Finding] | None = None) -> list[str]:
    """Return the reasons the transition is blocked (empty = allowed)."""
    errors: list[str] = []
    allowed = VALID_TRANSITIONS.get(model.state, set())
    if to_state not in allowed:
        errors.append(f"illegal lifecycle transition {model.state.value} -> {to_state.value}")
        return errors
    if to_state is ModelState.VALIDATED:
        errors.extend(model.validate())
        if model.risk_tier is None:
            errors.append("risk tier must be assigned before validation")
    if to_state is ModelState.APPROVED:
        approved = [a for a in (approvals or []) if a.state.value == "approved"]
        if not approved:
            errors.append("no approved deployment approval for this model")
    if to_state is ModelState.DEPLOYED:
        blocking = [f for f in (findings or [])
                    if not f.passed and f.severity is FindingSeverity.BLOCKING]
        if blocking:
            errors.append(
                "blocking findings: " + ", ".join(
                    f"{f.check_id} ({f.message})" for f in blocking
                )
            )
        approved = [a for a in (approvals or []) if a.state.value == "approved"]
        if not approved:
            errors.append("no approved deployment approval for this model")
    return errors


def transition(model: ModelRegistration, to_state: ModelState,
               approvals: list[Approval] | None = None,
               findings: list[Finding] | None = None) -> ModelRegistration:
    errors = gate_errors(model, to_state, approvals, findings)
    if errors:
        raise ValueError("; ".join(errors))
    model.state = to_state
    return model
