"""FastAPI surface for the AI governance platform.

The service is a thin HTTP wrapper over the pure-stdlib core: it owns
the in-memory stores, stamps timestamps, and translates domain
ValueErrors into 400 responses. All decision logic lives in the core
modules, so the API adds no second source of truth.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agov import approvals as approvals_mod
from agov import lifecycle as lifecycle_mod
from agov.approvals import Approval
from agov.audit import AuditLog
from agov.evidence import attach_evidence
from agov.lifecycle import ModelState
from agov.models import (
    ApprovalState,
    AutonomyLevel,
    DataSensitivity,
    Evidence,
    ModelRegistration,
)
from agov.policies import default_registry
from agov.reporting import compliance_report
from agov.risk import assign_risk_tier

SECRET = os.environ.get("AGOV_SIGNING_SECRET", "dev-only-signing-secret")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


app = FastAPI(title="AI Governance Platform", version="1.0.0")

# In-memory stores (a real deployment backs these with a database).
models: dict[str, ModelRegistration] = {}
evidence_store: dict[str, list[Evidence]] = {}
findings_store: dict[str, list] = {}
approvals_store: dict[str, Approval] = {}
audit = AuditLog()
policies = default_registry()


class RegisterModelRequest(BaseModel):
    model_id: str
    name: str
    version: str
    owner: str
    purpose: str
    use_case: str
    use_case_sensitivity: int
    data_sensitivity: DataSensitivity
    autonomy: AutonomyLevel
    user_impact: int
    reversibility: int
    tags: list[str] = []


class EvidenceRequest(BaseModel):
    evidence_id: str
    kind: str
    metric_name: str
    metric_value: float
    threshold: float
    passed: bool
    measured_at: str
    notes: str = ""


class ApprovalRequest(BaseModel):
    approval_id: str
    submitted_by: str
    target_state: str = "deployed"
    reason: str = ""


class DecisionRequest(BaseModel):
    approver: str
    decision: str  # approve | reject


class CheckRequest(BaseModel):
    today: str = ""
    human_oversight_documented: bool = False


def _model_or_404(model_id: str) -> ModelRegistration:
    model = models.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"unknown model {model_id}")
    return model


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "models": len(models)}


@app.post("/models", status_code=201)
def register_model(req: RegisterModelRequest) -> dict:
    if req.model_id in models:
        raise HTTPException(status_code=409, detail=f"model {req.model_id} already registered")
    model = ModelRegistration(**req.model_dump(), registered_at=_now())
    errors = model.validate()
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    assign_risk_tier(model)
    models[model.model_id] = model
    audit.append(_now(), req.owner, "model_registered", model.model_id,
                 f"tier={model.risk_tier.value} score={model.risk_score}")
    return {"model_id": model.model_id, "risk_tier": model.risk_tier.value,
            "risk_score": model.risk_score, "risk_rationale": model.risk_rationale}


@app.get("/models/{model_id}")
def get_model(model_id: str) -> dict:
    model = _model_or_404(model_id)
    return {
        "model_id": model.model_id, "name": model.name, "version": model.version,
        "owner": model.owner, "purpose": model.purpose, "use_case": model.use_case,
        "risk_tier": model.risk_tier.value if model.risk_tier else None,
        "risk_score": model.risk_score, "state": model.state.value,
        "last_reviewed_at": model.last_reviewed_at,
    }


@app.get("/models")
def list_models() -> dict:
    return {"models": [m.model_id for m in models.values()]}


@app.post("/models/{model_id}/evidence", status_code=201)
def add_evidence(model_id: str, req: EvidenceRequest) -> dict:
    _model_or_404(model_id)
    evidence = Evidence(model_id=model_id, **req.model_dump())
    try:
        attach_evidence(evidence_store, evidence)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.append(_now(), "governance", "evidence_attached", model_id,
                 f"{evidence.kind}:{evidence.metric_name}={evidence.metric_value}")
    return {"evidence_id": evidence.evidence_id, "passed": evidence.passed}


@app.post("/models/{model_id}/checks")
def run_checks(model_id: str, req: CheckRequest) -> dict:
    model = _model_or_404(model_id)
    # The check run itself is the review: stamp the review date first so
    # the review-currency check evaluates the review being performed.
    model.last_reviewed_at = req.today or _now()[:10]
    findings = policies.run_checks(
        model, evidence_store.get(model_id, []),
        ctx={"today": req.today or _now()[:10],
             "human_oversight_documented": req.human_oversight_documented},
        checked_at=_now(),
    )
    findings_store[model_id] = findings
    audit.append(_now(), "governance", "checks_run", model_id,
                 f"{sum(1 for f in findings if f.passed)}/{len(findings)} passed")
    return {
        "model_id": model_id,
        "findings": [
            {"check_id": f.check_id, "policy": f.policy_key, "passed": f.passed,
             "severity": f.severity.value, "message": f.message}
            for f in findings
        ],
    }


@app.post("/models/{model_id}/approvals", status_code=201)
def create_approval(model_id: str, req: ApprovalRequest) -> dict:
    _model_or_404(model_id)
    if req.approval_id in approvals_store:
        raise HTTPException(status_code=409, detail=f"approval {req.approval_id} already exists")
    approval = Approval(approval_id=req.approval_id, model_id=model_id,
                        target_state=req.target_state, submitted_by=req.submitted_by,
                        reason=req.reason, created_at=_now())
    approvals_store[req.approval_id] = approval
    audit.append(_now(), req.submitted_by, "approval_created", model_id, req.approval_id)
    return {"approval_id": approval.approval_id, "state": approval.state.value}


@app.post("/approvals/{approval_id}/submit")
def submit_approval(approval_id: str) -> dict:
    approval = approvals_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"unknown approval {approval_id}")
    try:
        approvals_mod.transition(approval, ApprovalState.SUBMITTED, approval.submitted_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"approval_id": approval_id, "state": approval.state.value}


@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, req: DecisionRequest) -> dict:
    approval = approvals_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"unknown approval {approval_id}")
    model = models[approval.model_id]
    try:
        approvals_mod.record_decision(approval, req.approver, req.decision, SECRET, _now())
        approvals_mod.resolve_quorum(approval, model.risk_tier, _now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.append(_now(), req.approver, f"approval_{req.decision}", approval.model_id, approval_id)
    return {"approval_id": approval_id, "state": approval.state.value,
            "approvals": approval.approver_count()}


@app.post("/models/{model_id}/lifecycle")
def lifecycle_transition(model_id: str, req: dict) -> dict:
    model = _model_or_404(model_id)
    try:
        to_state = ModelState(req.get("to_state", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown state {req.get('to_state')}") from exc
    model_approvals = [a for a in approvals_store.values() if a.model_id == model_id]
    try:
        lifecycle_mod.transition(model, to_state, approvals=model_approvals,
                                 findings=findings_store.get(model_id, []))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.append(_now(), req.get("actor", "governance"), "lifecycle_transition",
                 model_id, f"-> {to_state.value}")
    return {"model_id": model_id, "state": model.state.value}


@app.get("/compliance/report")
def compliance() -> dict:
    return compliance_report(list(models.values()), findings_store,
                             list(approvals_store.values()), _now()[:10])


@app.get("/audit/verify")
def audit_verify() -> dict:
    ok, message = audit.verify()
    return {"valid": ok, "message": message, "entries": len(audit.entries)}
