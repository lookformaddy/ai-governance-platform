"""Evidence validation and freshness tests."""
import pytest

from agov.evidence import attach_evidence, is_fresh, validate_evidence
from agov.models import Evidence


def _ev(**kw):
    base = dict(evidence_id="e1", model_id="m1", kind="drift", metric_name="psi",
                metric_value=0.08, threshold=0.20, passed=True, measured_at="2026-11-06")
    base.update(kw)
    return Evidence(**base)


def test_valid_evidence_passes():
    assert validate_evidence(_ev()) == []


def test_bad_kind_rejected():
    errors = validate_evidence(_ev(kind="vibes"))
    assert any("kind" in e for e in errors)


def test_passed_must_match_metric_vs_threshold():
    errors = validate_evidence(_ev(metric_value=0.30, passed=True))
    assert any("contradicts" in e for e in errors)
    assert validate_evidence(_ev(metric_value=0.30, passed=False)) == []


def test_bad_date_rejected():
    errors = validate_evidence(_ev(measured_at="not-a-date"))
    assert any("measured_at" in e for e in errors)


def test_attach_stores_and_rejects_invalid():
    store: dict = {}
    attach_evidence(store, _ev())
    assert len(store["m1"]) == 1
    with pytest.raises(ValueError):
        attach_evidence(store, _ev(evidence_id="e2", kind="nope"))
    assert len(store["m1"]) == 1  # invalid evidence never entered the store


def test_freshness_window():
    ev = _ev(measured_at="2026-10-20")
    assert is_fresh(ev, "2026-11-06") is True
    assert is_fresh(ev, "2026-11-20") is False  # 31 days > 30
    assert is_fresh(_ev(measured_at="garbage"), "2026-11-06") is False
