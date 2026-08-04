"""Contract tests for the unified multi-scenario facade."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentskill_eval_scenarios import (
    ComparisonKind,
    EvidenceClass,
    UnifiedScenarioRunner,
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


def test_software_engineering_rejects_process_agent() -> None:
    spec = UnifiedScenarioSpec.model_validate(
        {
            "name": "se-with-process-agent",
            "scenario": "software_engineering",
            "comparison": ComparisonKind.SKILL_AB,
            "native_config": "config.yaml",
            "simulated": True,
            "evidence_class": EvidenceClass.PROCESS_INTEGRATION,
            "claim_limit": "simulation only",
            "skill": {
                "name": "test",
                "version": "1.0",
                "activation_mode": "native_install",
                "path": str(ROOT / "examples/skills/python-review-v1"),
                "expected_sha256": "00" * 32,
            },
            "process_agent": {
                "name": "test",
                "version": "1.0",
                "executable": str(ROOT / "tests/fixtures/fake_scenario_agent.py"),
                "expected_sha256": "00" * 32,
                "expected_version_output": "v1",
                "timeout_seconds": 1,
            },
        }
    )
    with pytest.raises(ValueError, match="Skill content does not match expected_sha256"):
        UnifiedScenarioRunner(ROOT).validate(spec)