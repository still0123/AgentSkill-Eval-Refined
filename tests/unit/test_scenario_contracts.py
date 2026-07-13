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
                "schema_version": "ase/unified-scenario/v1alpha1",
                "name": "missing-skill",
                "scenario": "mcp_tool",
                "comparison": ComparisonKind.SKILL_AB,
                "native_config": "config.yaml",
                "simulated": True,
                "evidence_class": EvidenceClass.SIMULATED_CONTROLLER,
                "claim_limit": "simulation only",
            }
        )


def test_observed_evidence_cannot_be_marked_simulated() -> None:
    payload = UnifiedScenarioSpec.load(ROOT / "examples/unified/mcp-tool.yaml").model_dump()
    payload["evidence_class"] = EvidenceClass.OBSERVED_AGENT
    with pytest.raises(ValidationError, match="cannot be simulated"):
        UnifiedScenarioSpec.model_validate(payload)


def test_skill_hash_is_verified() -> None:
    spec = UnifiedScenarioSpec.load(ROOT / "examples/unified/mcp-tool.yaml")
    assert spec.skill is not None
    broken = spec.skill.model_copy(update={"expected_sha256": "0" * 64})
    with pytest.raises(ValueError, match="does not match"):
        broken.verify()


def test_adapter_rejects_evidence_boundary_different_from_native_runner() -> None:
    spec = UnifiedScenarioSpec.load(ROOT / "examples/unified/mcp-tool.yaml")
    mismatched = spec.model_copy(
        update={"simulated": False, "evidence_class": EvidenceClass.PROCESS_INTEGRATION}
    )
    with pytest.raises(ValueError, match="evidence boundary"):
        UnifiedScenarioRunner(ROOT).validate(mismatched)
