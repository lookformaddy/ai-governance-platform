"""Hash-chained audit log and compliance reporting tests."""
from agov.audit import AuditLog
from agov.models import (
    Approval,
    ApprovalState,
    AutonomyLevel,
    DataSensitivity,
    Finding,
    FindingSeverity,
    ModelRegistration,
)
from agov.reporting import compliance_report
from agov.risk import assign_risk_tier


def _model(model_id, **kw):
    base = dict(model_id=model_id, name="M", version="1.0.0", owner="a.chen",
                purpose="p", use_case="document summarization",
                use_case_sensitivity=1, data_sensitivity=DataSensitivity.PUBLIC,
                autonomy=AutonomyLevel.HUMAN_IN_LOOP, user_impact=1, reversibility=0)
    base.update(kw)
    m = ModelRegistration(**base)
    assign_risk_tier(m)
    return m


def test_audit_chain_verifies():
    log = AuditLog()
    log.append("2026-11-06", "a.chen", "model_registered", "m1")
    log.append("2026-11-06", "governance", "checks_run", "m1")
    ok, msg = log.verify()
    assert ok and "2 entries" in msg


def test_audit_tamper_detected():
    log = AuditLog()
    log.append("2026-11-06", "a.chen", "model_registered", "m1")
    log.append("2026-11-06", "governance", "checks_run", "m1")
    log.entries[0].detail = "forged detail"
    ok, msg = log.verify()
    assert not ok and "hash mismatch" in msg


def test_audit_reorder_detected():
    log = AuditLog()
    log.append("2026-11-06", "a.chen", "model_registered", "m1")
    log.append("2026-11-06", "governance", "checks_run", "m1")
    log.entries[0], log.entries[1] = log.entries[1], log.entries[0]
    ok, _ = log.verify()
    assert not ok


def test_audit_for_model_filters():
    log = AuditLog()
    log.append("2026-11-06", "a.chen", "model_registered", "m1")
    log.append("2026-11-06", "a.chen", "model_registered", "m2")
    assert [e.model_id for e in log.for_model("m1")] == ["m1"]


def test_compliance_report_coverage_and_tiers():
    m1 = _model("m1")
    m1.last_reviewed_at = "2026-11-06"
    m2 = _model("m2", use_case="eligibility scoring", use_case_sensitivity=9,
                data_sensitivity=DataSensitivity.REGULATED,
                autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9)
    m2.last_reviewed_at = "2026-01-01"  # overdue for high-risk (90d)
    passing = Finding("owner_assigned", "gov-basics@v1", "m1", True,
                      FindingSeverity.INFO, "ok", "2026-11-06")
    blocking = Finding("high_risk_has_drift_evidence", "high-risk-evidence@v1", "m2",
                       False, FindingSeverity.BLOCKING, "no drift evidence", "2026-11-06")
    approval = Approval("appr-1", "m2", "deployed", "a.chen", ApprovalState.APPROVED)
    report = compliance_report(
        [m1, m2], {"m1": [passing], "m2": [blocking]}, [approval], "2026-11-06")
    assert report["total_models"] == 2
    assert report["coverage_pct"] == 100.0
    assert report["open_findings"] == 1
    assert report["blocking_findings"] == 1
    assert report["overdue_reviews"] == 1
    assert report["overdue_model_ids"] == ["m2"]
    assert report["by_tier"]["high"]["models"] == 1
    assert report["by_tier"]["minimal"]["models"] == 1
    assert report["approvals_by_state"] == {"approved": 1}


def test_compliance_report_empty_inventory():
    report = compliance_report([], {}, [], "2026-11-06")
    assert report["total_models"] == 0
    assert report["coverage_pct"] == 100.0
    assert report["open_findings"] == 0
