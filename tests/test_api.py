"""API tests. Auto-skips when FastAPI is not installed (runs in CI)."""
import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI not installed")
from fastapi.testclient import TestClient  # noqa: E402

from agov.service import app  # noqa: E402

HIGH = {
    "model_id": "api-high",
    "name": "Eligibility Scorer",
    "version": "1.0.0",
    "owner": "a.chen",
    "purpose": "Score applications for eligibility review",
    "use_case": "eligibility scoring",
    "use_case_sensitivity": 9,
    "data_sensitivity": "regulated",
    "autonomy": "human_on_loop",
    "user_impact": 9,
    "reversibility": 8,
}


def _register(client, payload):
    resp = client.post("/models", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health():
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_register_assigns_high_tier():
    data = _register(TestClient(app), HIGH)
    assert data["risk_tier"] == "high"
    assert data["risk_score"] == 85


def test_register_duplicate_conflict():
    client = TestClient(app)
    _register(client, {**HIGH, "model_id": "api-dup"})
    assert client.post("/models", json={**HIGH, "model_id": "api-dup"}).status_code == 409


def test_register_invalid_scores_rejected():
    client = TestClient(app)
    bad = {**HIGH, "model_id": "api-bad", "use_case_sensitivity": 99}
    assert client.post("/models", json=bad).status_code == 400  # domain validate() rejects out-of-range scores


def test_full_governance_flow_via_api():
    client = TestClient(app)
    mid = "api-flow"
    _register(client, {**HIGH, "model_id": mid})
    # Evidence first: without it, deployment is blocked.
    for ev in (
        {"evidence_id": "ev1", "kind": "drift", "metric_name": "psi",
         "metric_value": 0.08, "threshold": 0.20, "passed": True, "measured_at": "2026-11-06"},
        {"evidence_id": "ev2", "kind": "fairness", "metric_name": "demographic_parity_gap",
         "metric_value": 0.03, "threshold": 0.10, "passed": True, "measured_at": "2026-11-06"},
    ):
        assert client.post(f"/models/{mid}/evidence", json=ev).status_code == 201
    checks = client.post(f"/models/{mid}/checks",
                         json={"today": "2026-11-06", "human_oversight_documented": True}).json()
    assert all(f["passed"] for f in checks["findings"]), checks
    # Deploy before approval must fail the lifecycle gate.
    assert client.post(f"/models/{mid}/lifecycle",
                       json={"to_state": "deployed", "actor": "a.chen"}).status_code == 400
    # Approval: submit, two approvers (quorum 2 for high risk).
    assert client.post(f"/models/{mid}/approvals",
                       json={"approval_id": "appr-api", "submitted_by": "a.chen"}).status_code == 201
    assert client.post("/approvals/appr-api/submit").json()["state"] == "submitted"
    assert client.post("/approvals/appr-api/decide",
                       json={"approver": "a.chen", "decision": "approve"}).status_code == 400
    assert client.post("/approvals/appr-api/decide",
                       json={"approver": "d.kim", "decision": "approve"}).json()["state"] == "in_review"
    final = client.post("/approvals/appr-api/decide",
                        json={"approver": "e.santos", "decision": "approve"}).json()
    assert final["state"] == "approved"
    # Lifecycle gates now pass.
    for state in ("validated", "approved", "deployed", "monitoring"):
        resp = client.post(f"/models/{mid}/lifecycle",
                           json={"to_state": state, "actor": "governance"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == state
    report = client.get("/compliance/report").json()
    assert report["total_models"] >= 1
    assert report["blocking_findings"] == 0
    verify = client.get("/audit/verify").json()
    assert verify["valid"] is True
    assert verify["entries"] >= 1
