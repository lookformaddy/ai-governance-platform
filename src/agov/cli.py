"""CLI: register a model, run checks, and print the compliance report.

Usage:
    PYTHONPATH=src python -m agov.cli register --model-id m1 --name "Eligibility Scorer" ...
    PYTHONPATH=src python -m agov.cli report
"""
from __future__ import annotations

import argparse
import json

from agov.models import AutonomyLevel, DataSensitivity, ModelRegistration
from agov.policies import default_registry
from agov.reporting import compliance_report
from agov.risk import assign_risk_tier


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agov", description="AI Governance Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register", help="Register a model and assign its risk tier")
    reg.add_argument("--model-id", required=True)
    reg.add_argument("--name", required=True)
    reg.add_argument("--owner", required=True)
    reg.add_argument("--purpose", required=True)
    reg.add_argument("--use-case", required=True)
    reg.add_argument("--use-case-sensitivity", type=int, default=5)
    reg.add_argument("--data-sensitivity", default="internal",
                     choices=[d.value for d in DataSensitivity])
    reg.add_argument("--autonomy", default="human_on_loop",
                     choices=[a.value for a in AutonomyLevel])
    reg.add_argument("--user-impact", type=int, default=5)
    reg.add_argument("--reversibility", type=int, default=5)
    sub.add_parser("report", help="Print an empty compliance report template")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "register":
        model = ModelRegistration(
            model_id=args.model_id, name=args.name, version="1.0.0",
            owner=args.owner, purpose=args.purpose, use_case=args.use_case,
            use_case_sensitivity=args.use_case_sensitivity,
            data_sensitivity=DataSensitivity(args.data_sensitivity),
            autonomy=AutonomyLevel(args.autonomy),
            user_impact=args.user_impact, reversibility=args.reversibility,
        )
        errors = model.validate()
        if errors:
            print("invalid: " + "; ".join(errors))
            return 2
        assign_risk_tier(model)
        print(json.dumps({
            "model_id": model.model_id, "risk_tier": model.risk_tier.value,
            "risk_score": model.risk_score, "risk_rationale": model.risk_rationale,
        }, indent=2))
        return 0
    if args.command == "report":
        print(json.dumps(compliance_report([], {}, [], "2026-11-06"), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
