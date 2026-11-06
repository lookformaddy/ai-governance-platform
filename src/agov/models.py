"""Core domain models.

Design notes
------------
* Everything is a plain dataclass with no I/O, so the domain logic is
  testable without a database or a network.
* A model registration is the unit of governance: one registered model
  version, one owner, one declared purpose. Governance state (risk tier,
  policy results, approvals, evidence) hangs off it, never inside free
  text, so reporting can aggregate it without parsing prose.
* Timestamps are plain ISO strings in the dataclasses; the service layer
  stamps them. The core never calls ``datetime.now`` except through an
  explicit ``now`` argument, which keeps tests deterministic.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RiskTier(enum.Enum):
    """Risk tier assigned to a registered model (EU-AI-Act-style)."""

    MINIMAL = "minimal"
    LIMITED = "limited"
    HIGH = "high"
    UNACCEPTABLE = "unacceptable"

    @property
    def review_cadence_days(self) -> int:
        """How often the model must be re-reviewed, in days."""
        return {
            "minimal": 365,
            "limited": 180,
            "high": 90,
            "unacceptable": 0,
        }[self.value]

    @property
    def required_approvers(self) -> int:
        """Quorum of distinct approvers needed to approve a deployment."""
        return {"minimal": 0, "limited": 1, "high": 2, "unacceptable": 0}[self.value]

    @property
    def rank(self) -> int:
        return {"minimal": 1, "limited": 2, "high": 3, "unacceptable": 4}[self.value]


class DataSensitivity(enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    REGULATED = "regulated"  # e.g. personal / health / financial data


class AutonomyLevel(enum.Enum):
    HUMAN_IN_LOOP = "human_in_loop"      # human approves every output
    HUMAN_ON_LOOP = "human_on_loop"      # human supervises, can override
    AUTONOMOUS = "autonomous"            # acts without human review


class ModelState(enum.Enum):
    REGISTERED = "registered"
    VALIDATED = "validated"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    MONITORING = "monitoring"
    RETIRED = "retired"


class DeploymentState(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"


class ApprovalState(enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class FindingSeverity(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass
class ModelRegistration:
    """One registered model version in the inventory."""

    model_id: str
    name: str
    version: str
    owner: str
    purpose: str
    use_case: str  # free text, e.g. "eligibility scoring"
    use_case_sensitivity: int  # 0-10: how sensitive the decision is
    data_sensitivity: DataSensitivity
    autonomy: AutonomyLevel
    user_impact: int  # 0-10: breadth/severity of impact on people
    reversibility: int  # 0-10: 0 = easily reversible, 10 = irreversible
    risk_tier: RiskTier | None = None
    risk_score: int | None = None
    risk_rationale: str = ""
    state: ModelState = ModelState.REGISTERED
    registered_at: str = ""
    last_reviewed_at: str = ""
    tags: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.model_id.strip():
            errors.append("model_id is required")
        if not self.name.strip():
            errors.append("name is required")
        if not self.version.strip():
            errors.append("version is required")
        if not self.owner.strip():
            errors.append("owner is required")
        if not self.purpose.strip():
            errors.append("purpose is required")
        for fname in ("use_case_sensitivity", "user_impact", "reversibility"):
            value = getattr(self, fname)
            if not isinstance(value, int) or not 0 <= value <= 10:
                errors.append(f"{fname} must be an integer in 0..10")
        return errors


@dataclass
class Policy:
    """A versioned governance policy. Immutable once published: a change
    ships as a new version, so old check results stay interpretable."""

    policy_id: str
    name: str
    version: int
    description: str
    check_ids: list[str]  # ids of checks in the check registry
    applies_to_tiers: list[RiskTier]
    effective_from: str = ""
    superseded_by: int | None = None

    @property
    def key(self) -> str:
        return f"{self.policy_id}@v{self.version}"


@dataclass
class Finding:
    """One policy-check outcome."""

    check_id: str
    policy_key: str
    model_id: str
    passed: bool
    severity: FindingSeverity
    message: str
    checked_at: str = ""


@dataclass
class Evidence:
    """Drift / fairness / performance evidence attached to a model."""

    evidence_id: str
    model_id: str
    kind: str  # "drift" | "fairness" | "performance" | "security"
    metric_name: str
    metric_value: float
    threshold: float
    passed: bool
    measured_at: str
    notes: str = ""


@dataclass
class Approval:
    """A deployment approval request with signed approver decisions."""

    approval_id: str
    model_id: str
    target_state: str  # e.g. "deployed"
    submitted_by: str
    state: ApprovalState = ApprovalState.DRAFT
    decisions: list[dict] = field(default_factory=list)  # {approver, decision, signature, at}
    reason: str = ""
    created_at: str = ""
    decided_at: str = ""

    def approver_count(self) -> int:
        return len({d["approver"] for d in self.decisions if d["decision"] == "approve"})

    def rejecter_count(self) -> int:
        return len({d["approver"] for d in self.decisions if d["decision"] == "reject"})


@dataclass
class AuditEntry:
    """One hash-chained audit record."""

    seq: int
    timestamp: str
    actor: str
    action: str
    model_id: str
    detail: str
    prev_hash: str
    entry_hash: str
