"""Risk-tier scoring tests."""
from agov.models import AutonomyLevel, DataSensitivity, ModelRegistration
from agov.risk import assign_risk_tier, is_review_overdue, score_model


def _model(**kw):
    base = dict(model_id="m1", name="M", version="1.0.0", owner="a.chen",
                purpose="p", use_case="document summarization",
                use_case_sensitivity=1, data_sensitivity=DataSensitivity.PUBLIC,
                autonomy=AutonomyLevel.HUMAN_IN_LOOP, user_impact=1, reversibility=0)
    base.update(kw)
    return ModelRegistration(**base)


def test_minimal_low_scores():
    m = _model()
    assert assign_risk_tier(m).value == "minimal"
    assert m.risk_score == 1 * 4 + 0 + 0 + 1  # 5


def test_high_risk_threshold():
    m = _model(use_case="eligibility scoring", use_case_sensitivity=9,
               data_sensitivity=DataSensitivity.REGULATED,
               autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9)
    assert assign_risk_tier(m).value == "high"
    assert m.risk_score == 36 + 30 + 10 + 9  # 85


def test_limited_mid_scores():
    m = _model(use_case="support ticket triage", use_case_sensitivity=5,
               data_sensitivity=DataSensitivity.INTERNAL,
               autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=5)
    assert assign_risk_tier(m).value == "limited"
    assert m.risk_score == 20 + 10 + 10 + 5  # 45


def test_prohibited_use_case_overrides_score():
    m = _model(use_case="Social Scoring", use_case_sensitivity=0,
               data_sensitivity=DataSensitivity.PUBLIC, user_impact=0)
    assert assign_risk_tier(m).value == "unacceptable"
    assert m.risk_score == 100
    assert "Prohibited" in m.risk_rationale


def test_score_is_capped_and_explainable():
    score, lines = score_model(_model(use_case_sensitivity=10, user_impact=10,
                                      data_sensitivity=DataSensitivity.REGULATED,
                                      autonomy=AutonomyLevel.AUTONOMOUS))
    assert score == 100  # 40+30+20+10 capped
    assert len(lines) == 4


def test_review_overdue_fail_closed():
    m = _model()
    assert is_review_overdue(m, "2026-11-06") is True  # no tier, no review
    assign_risk_tier(m)
    assert is_review_overdue(m, "2026-11-06") is True  # no review date
    m.last_reviewed_at = "2026-11-06"
    assert is_review_overdue(m, "2026-11-06") is False
    assert is_review_overdue(m, "2027-11-07") is True  # minimal cadence 365d


def test_high_risk_cadence_90_days():
    m = _model(use_case="eligibility scoring", use_case_sensitivity=9,
               data_sensitivity=DataSensitivity.REGULATED,
               autonomy=AutonomyLevel.HUMAN_ON_LOOP, user_impact=9)
    assign_risk_tier(m)
    m.last_reviewed_at = "2026-08-01"
    assert is_review_overdue(m, "2026-10-30") is False  # 90 days exactly
    assert is_review_overdue(m, "2026-10-31") is True


def test_model_validation_errors():
    m = _model(model_id="", use_case_sensitivity=11, user_impact=-1)
    errors = m.validate()
    assert "model_id is required" in errors
    assert any("use_case_sensitivity" in e for e in errors)
    assert any("user_impact" in e for e in errors)
