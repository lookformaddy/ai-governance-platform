"""Risk-tier assignment.

Deterministic, explainable scoring — no model, no network. The score is
a weighted sum of four declared factors, each 0-10 except data
sensitivity, which is mapped from its enum. A prohibited use case
overrides the score entirely and lands in UNACCEPTABLE: the platform
never lets a score argue a banned use case into deployment.

Weights (max 100):
  use-case sensitivity  x4   (0-40)  — how consequential the decision is
  data sensitivity      mapped      — public 0, internal 10, confidential 20, regulated 30
  autonomy              x2   (0-20)  — human_in_loop 0, human_on_loop 10, autonomous 20
  user impact           x1   (0-10)
Thresholds: >= 70 HIGH, >= 40 LIMITED, else MINIMAL. UNACCEPTABLE only
via the prohibited-use-case list.
"""
from __future__ import annotations

from agov.models import AutonomyLevel, DataSensitivity, ModelRegistration, RiskTier

DATA_SENSITIVITY_SCORE = {
    DataSensitivity.PUBLIC: 0,
    DataSensitivity.INTERNAL: 10,
    DataSensitivity.CONFIDENTIAL: 20,
    DataSensitivity.REGULATED: 30,
}

AUTONOMY_SCORE = {
    AutonomyLevel.HUMAN_IN_LOOP: 0,
    AutonomyLevel.HUMAN_ON_LOOP: 10,
    AutonomyLevel.AUTONOMOUS: 20,
}

HIGH_THRESHOLD = 70
LIMITED_THRESHOLD = 40

# Generic prohibited use cases (EU AI Act Annex III-style, paraphrased).
PROHIBITED_USE_CASES = frozenset(
    {
        "social scoring",
        "subliminal manipulation",
        "exploitation of vulnerability",
        "untargeted biometric identification",
        "emotion inference in workplace",
        "emotion inference in education",
    }
)


def _normalise_use_case(use_case: str) -> str:
    return " ".join(use_case.strip().lower().split())


def score_model(model: ModelRegistration) -> tuple[int, list[str]]:
    """Return (score 0-100, rationale lines)."""
    lines = [
        f"use_case_sensitivity {model.use_case_sensitivity}/10 x4 = {model.use_case_sensitivity * 4}",
        f"data_sensitivity {model.data_sensitivity.value} = "
        f"{DATA_SENSITIVITY_SCORE[model.data_sensitivity]}",
        f"autonomy {model.autonomy.value} = {AUTONOMY_SCORE[model.autonomy]}",
        f"user_impact {model.user_impact}/10 x1 = {model.user_impact}",
    ]
    score = (
        model.use_case_sensitivity * 4
        + DATA_SENSITIVITY_SCORE[model.data_sensitivity]
        + AUTONOMY_SCORE[model.autonomy]
        + model.user_impact
    )
    return min(100, score), lines


def assign_risk_tier(model: ModelRegistration) -> RiskTier:
    """Score the model, set risk_tier/risk_score/risk_rationale in place,
    and return the tier. Prohibited use cases short-circuit to
    UNACCEPTABLE with a score of 100."""
    use_case = _normalise_use_case(model.use_case)
    if use_case in PROHIBITED_USE_CASES:
        model.risk_score = 100
        model.risk_tier = RiskTier.UNACCEPTABLE
        model.risk_rationale = (
            f"Prohibited use case '{model.use_case.strip()}': "
            "unacceptable risk regardless of score."
        )
        return model.risk_tier
    score, lines = score_model(model)
    if score >= HIGH_THRESHOLD:
        tier = RiskTier.HIGH
    elif score >= LIMITED_THRESHOLD:
        tier = RiskTier.LIMITED
    else:
        tier = RiskTier.MINIMAL
    model.risk_score = score
    model.risk_tier = tier
    model.risk_rationale = (
        f"score {score}/100 ({'; '.join(lines)}); "
        f"thresholds: HIGH >= {HIGH_THRESHOLD}, LIMITED >= {LIMITED_THRESHOLD}."
    )
    return tier


def is_review_overdue(model: ModelRegistration, today: str) -> bool:
    """True when the model's last review is older than its tier cadence.
    Models with no tier or no review date are treated as overdue —
    fail closed, never as 'up to date'."""
    from datetime import date

    if model.risk_tier is None or not model.last_reviewed_at:
        return True
    if model.risk_tier is RiskTier.UNACCEPTABLE:
        return True
    reviewed = date.fromisoformat(model.last_reviewed_at[:10])
    current = date.fromisoformat(today[:10])
    return (current - reviewed).days > model.risk_tier.review_cadence_days
