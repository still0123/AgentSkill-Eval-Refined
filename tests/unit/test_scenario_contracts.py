"""Contract tests for the unified multi-scenario facade."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentskill_eval_scenarios import (
    ComparisonKind,
    EvidenceClass,
    UnifiedScenarioSpec,
)

ROOT = Path(__file__).resolve().parents[2]


def test_skill_ab_requires_frozen_skill() -> None:
    with pytest.raises(ValidationError, match="frozen Skill"):
        UnifiedScenarioSpec.model_validate(
            {
                "name": "missing-skill",
                "scenario": "software_engineering",
                "comparison": ComparisonKind.SKILL_AB,
                "native_config": "config.yaml",
                "simulated": True,
                "evidence_class": EvidenceClass.SIMULATED_CONTROLLER,
                "claim_limit": "simulation only",
            }
        )


def test_observed_evidence_cannot_be_marked_simulated() -> None:
    path = ROOT / "examples/unified/software-engineering.yaml"
    if not path.exists():
        pytest.skip("no software-engineering example config")
    payload = UnifiedScenarioSpec.load(path).model_dump()
    payload["evidence_class"] = EvidenceClass.OBSERVED_AGENT
    with pytest.raises(ValidationError, match="cannot be simulated"):
        UnifiedScenarioSpec.model_validate(payload)


def test_software_engineering_requires_frozen_skill() -> None:
    with pytest.raises(ValidationError, match="frozen Skill"):
        UnifiedScenarioSpec.model_validate(
            {
                "name": "no-skill-se",
                "scenario": "software_engineering",
                "comparison": ComparisonKind.SKILL_AB,
                "native_config": "config.yaml",
                "simulated": True,
                "evidence_class": EvidenceClass.SIMULATED_CONTROLLER,
                "claim_limit": "simulation only",
            }
        )
