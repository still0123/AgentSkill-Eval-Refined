"""Strict configuration for observed-Agent evidence experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentskill_eval_contracts import RealEvidenceClass


class RealEvidenceSpecError(ValueError):
    """Raised when real-evidence configuration is unsafe or incomplete."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutableSpec(StrictModel):
    path: Path
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_version: str = Field(min_length=1)
    version_args: Tuple[str, ...] = ("--version",)


class RunnerSpec(ExecutableSpec):
    type: Literal["skill_up"] = "skill_up"
    name: Literal["skill-up"] = "skill-up"


class AgentSpec(ExecutableSpec):
    engine: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    seed: Optional[int] = None
    max_turns: int = Field(ge=1, le=100)
    timeout_seconds: int = Field(ge=1, le=7200)
    tool_capabilities: Tuple[str, ...] = Field(min_length=1)
    secret_env_names: Tuple[str, ...] = Field(min_length=1)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)

    @model_validator(mode="after")
    def names_are_unique(self) -> "AgentSpec":
        if len(set(self.tool_capabilities)) != len(self.tool_capabilities):
            raise ValueError("tool capabilities must be unique")
        if len(set(self.secret_env_names)) != len(self.secret_env_names):
            raise ValueError("secret environment names must be unique")
        for name in self.secret_env_names:
            if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
                raise ValueError(f"invalid secret environment name: {name!r}")
        return self


class PricingSpec(StrictModel):
    input_microusd_per_million_tokens: int = Field(ge=0)
    output_microusd_per_million_tokens: int = Field(ge=0)
    estimated_input_tokens_per_run: int = Field(ge=1)
    estimated_output_tokens_per_run: int = Field(ge=1)

    @property
    def estimated_cost_per_run_microusd(self) -> int:
        numerator = (
            self.estimated_input_tokens_per_run * self.input_microusd_per_million_tokens
            + self.estimated_output_tokens_per_run * self.output_microusd_per_million_tokens
        )
        return max(1, (numerator + 999_999) // 1_000_000)


class ProtocolSpec(StrictModel):
    evidence_repeats: int = Field(default=3, ge=3, le=20)
    random_seed: int = 2026
    bootstrap_resamples: int = Field(default=2_000, ge=100, le=100_000)
    min_independent_groups: int = Field(default=3, ge=1)


class RealAgentEvidenceSpec(StrictModel):
    schema_version: Literal["ase/real-agent-evidence/v1alpha1"]
    name: str = Field(min_length=1)
    dataset_path: Path
    skill_path: Path
    case_ids: Tuple[str, ...] = Field(min_length=2, max_length=2)
    evidence_class: RealEvidenceClass
    simulated: bool
    runner: RunnerSpec
    agent: AgentSpec
    pricing: PricingSpec
    protocol: ProtocolSpec = ProtocolSpec()
    sandbox_profile: str = Field(default="runner_default", min_length=1)
    network_policy: str = Field(default="provider_only", min_length=1)

    @model_validator(mode="after")
    def evidence_boundary_is_explicit(self) -> "RealAgentEvidenceSpec":
        integration = self.evidence_class == RealEvidenceClass.PROCESS_INTEGRATION
        if self.simulated != integration:
            raise ValueError(
                "process_integration requires simulated=true; observed_agent requires false"
            )
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case IDs must be unique")
        return self

    @classmethod
    def load(cls, path: Path) -> "RealAgentEvidenceSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise RealEvidenceSpecError(f"invalid real Agent evidence spec {path}: {exc}") from exc
