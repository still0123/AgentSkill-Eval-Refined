"""Shared contracts for running heterogeneous evaluation scenarios."""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

import yaml
from pydantic import Field, JsonValue, model_validator

from agentskill_eval_contracts import FrozenModel, stable_sha256


class ScenarioKind(str, Enum):
    SOFTWARE_ENGINEERING = "software_engineering"
    MCP_TOOL = "mcp_tool"
    MEMORY_RAG = "memory_rag"


class ComparisonKind(str, Enum):
    SKILL_AB = "skill_ab"
    COMPONENT_AB = "component_ab"
    STRESS_TEST = "stress_test"


class EvidenceClass(str, Enum):
    SIMULATED_CONTROLLER = "simulated_controller"
    PROCESS_INTEGRATION = "process_integration"
    OBSERVED_AGENT = "observed_agent"


class VariantDescriptor(FrozenModel):
    name: str = Field(min_length=1)
    role: Literal["control", "treatment"]
    skill_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SkillUnderTest(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: Path
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_mode: Literal["native_install", "precompiled_plan", "process_prompt"]

    def verify(self) -> Path:
        expanded = self.path.expanduser()
        if expanded.is_symlink():
            raise ValueError("symbolic-link Skill inputs are not allowed")
        root = expanded.resolve(strict=True)
        skill_file = root / "SKILL.md"
        if not root.is_dir() or root.is_symlink() or not skill_file.is_file():
            raise ValueError("Skill path must contain a regular SKILL.md")
        if skill_file.is_symlink():
            raise ValueError("symbolic-link SKILL.md inputs are not allowed")
        digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
        if digest != self.expected_sha256:
            raise ValueError("Skill content does not match expected_sha256")
        return root


class UnifiedScenarioSpec(FrozenModel):
    schema_version: Literal["ase/unified-scenario/v1alpha1"]
    name: str = Field(min_length=1)
    scenario: ScenarioKind
    comparison: ComparisonKind
    native_config: Path
    skill: Optional[SkillUnderTest] = None
    process_agent: Optional["ProcessScenarioAgentSpec"] = None
    simulated: bool
    evidence_class: EvidenceClass
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_and_target_must_be_consistent(self) -> "UnifiedScenarioSpec":
        if self.comparison == ComparisonKind.SKILL_AB and self.skill is None:
            raise ValueError("skill_ab comparisons require a frozen Skill")
        if self.evidence_class == EvidenceClass.OBSERVED_AGENT and self.simulated:
            raise ValueError("observed_agent evidence cannot be simulated")
        if self.evidence_class == EvidenceClass.SIMULATED_CONTROLLER and not self.simulated:
            raise ValueError("simulated_controller evidence requires simulated=true")
        if self.evidence_class == EvidenceClass.PROCESS_INTEGRATION and (
            not self.simulated or self.process_agent is None
        ):
            raise ValueError(
                "process_integration requires simulated=true and a pinned process_agent"
            )
        if (
            self.process_agent is not None
            and self.evidence_class != EvidenceClass.PROCESS_INTEGRATION
        ):
            raise ValueError("process_agent is only valid for process_integration evidence")
        return self

    @classmethod
    def load(cls, path: Path) -> "UnifiedScenarioSpec":
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise ValueError("symbolic-link scenario specs are not allowed")
        resolved = expanded.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("scenario spec must be a regular file")
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        spec = cls.model_validate(payload)
        native = spec.native_config
        if not native.is_absolute():
            native = resolved.parent / native
        if native.is_symlink():
            raise ValueError("symbolic-link native configs are not allowed")
        native = native.resolve(strict=True)
        if not native.is_file():
            raise ValueError("native config must be a regular file")
        skill = spec.skill
        if skill is not None and not skill.path.is_absolute():
            skill = skill.model_copy(update={"path": resolved.parent / skill.path})
        process_agent = spec.process_agent
        if process_agent is not None and not process_agent.executable.is_absolute():
            process_agent = process_agent.model_copy(
                update={"executable": resolved.parent / process_agent.executable}
            )
        return spec.model_copy(
            update={"native_config": native, "skill": skill, "process_agent": process_agent}
        )


class ProcessScenarioAgentSpec(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    executable: Path
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    argv: Tuple[str, ...] = ()
    version_args: Tuple[str, ...] = ("--version",)
    expected_version_output: str = Field(min_length=1)
    timeout_seconds: float = Field(default=10, gt=0, le=600)
    max_response_bytes: int = Field(default=1_000_000, ge=1, le=10_000_000)
    allowed_environment: Tuple[str, ...] = ("PATH", "LANG", "LC_ALL")
    interaction_mode: Literal["plan_once", "step_loop"] = "plan_once"
    max_steps: int = Field(default=12, ge=1, le=50)
    max_history_events: int = Field(default=24, ge=1, le=200)
    max_observation_bytes: int = Field(default=100_000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def environment_must_not_include_secrets(self) -> "ProcessScenarioAgentSpec":
        if len(set(self.allowed_environment)) != len(self.allowed_environment):
            raise ValueError("allowed_environment values must be unique")
        secret_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
        if any(
            marker in name.upper() for name in self.allowed_environment for marker in secret_markers
        ):
            raise ValueError("Process Scenario Agent cannot inherit Secret-like environment names")
        return self


class EvaluationPlan(FrozenModel):
    schema_version: Literal["ase/evaluation-plan/v1alpha1"] = "ase/evaluation-plan/v1alpha1"
    name: str
    scenario: ScenarioKind
    comparison: ComparisonKind
    native_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_name: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    agent: str = Field(min_length=1)
    model: str = Field(min_length=1)
    agent_version: Optional[str] = None
    agent_executable_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    interaction_mode: Literal["plan_once", "step_loop"] = "plan_once"
    max_interaction_steps: Optional[int] = Field(default=None, ge=1, le=50)
    skill_name: Optional[str] = None
    skill_version: Optional[str] = None
    skill_activation_mode: Optional[
        Literal["native_install", "precompiled_plan", "process_prompt"]
    ] = None
    variants: Tuple[VariantDescriptor, VariantDescriptor]
    simulated: bool
    evidence_class: EvidenceClass
    trace_capabilities: Tuple[str, ...]
    claim_limit: str

    @model_validator(mode="after")
    def variants_must_form_one_pair(self) -> "EvaluationPlan":
        if {item.role for item in self.variants} != {"control", "treatment"}:
            raise ValueError("plan requires exactly one control and one treatment")
        return self

    @property
    def plan_sha256(self) -> str:
        return stable_sha256(self.model_dump(mode="json"))


class ArtifactReference(FrozenModel):
    kind: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UnifiedEvaluationResult(FrozenModel):
    schema_version: Literal["ase/unified-result/v1alpha1"] = "ase/unified-result/v1alpha1"
    experiment_id: UUID
    plan: EvaluationPlan
    status: Literal["completed", "invalid"]
    primary_metrics: Dict[str, JsonValue]
    scenario_metrics: Dict[str, JsonValue]
    artifacts: Tuple[ArtifactReference, ...]
    capability_unavailable: Tuple[str, ...] = ()
    simulated: bool
    evidence_class: EvidenceClass
    claim_limit: str

    @model_validator(mode="after")
    def evidence_boundary_must_match_plan(self) -> "UnifiedEvaluationResult":
        if self.simulated != self.plan.simulated or self.evidence_class != self.plan.evidence_class:
            raise ValueError("result evidence boundary does not match its frozen plan")
        return self


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
