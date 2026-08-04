"""Experiment, variant, and paired-block contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Final, Optional, Tuple
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, computed_field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest, stable_sha256
from agentskill_eval_contracts.enums import ExperimentStatus, VariantRole
from agentskill_eval_contracts.snapshots import (
    AgentSnapshot,
    PriceSnapshot,
    RunnerSnapshot,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
)

SCHEMA_VERSION: Final = "ase/v1alpha1"


class ExperimentVariant(FrozenModel):
    id: UUID
    experiment_id: UUID
    name: str = Field(min_length=1, max_length=120)
    role: VariantRole
    runner_snapshot: RunnerSnapshot
    agent_snapshot: AgentSnapshot
    skill_snapshot: Optional[SkillSnapshot] = None
    tool_snapshot: ToolSnapshot
    sandbox_snapshot: SandboxSnapshot
    price_snapshot: Optional[PriceSnapshot] = None

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def variant_sha256(self) -> str:
        payload = {
            "role": self.role.value,
            "runner_snapshot": self.runner_snapshot.model_dump(mode="json"),
            "agent_snapshot": self.agent_snapshot.model_dump(mode="json"),
            "skill_snapshot": (
                self.skill_snapshot.model_dump(mode="json", exclude={"skill_id", "version_id"})
                if self.skill_snapshot
                else None
            ),
            "tool_snapshot": self.tool_snapshot.model_dump(mode="json"),
            "sandbox_snapshot": self.sandbox_snapshot.model_dump(mode="json"),
            "price_snapshot": (
                self.price_snapshot.model_dump(mode="json") if self.price_snapshot else None
            ),
        }
        return stable_sha256(payload)


class PairBlock(FrozenModel):
    id: UUID
    experiment_id: UUID
    case_id: UUID
    independence_group: str = Field(min_length=1, max_length=200)
    repeat_index: int = Field(ge=0)
    seed: Optional[int] = None
    execution_order: Tuple[UUID, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def execution_order_must_be_unique(self) -> "PairBlock":
        if len(set(self.execution_order)) != len(self.execution_order):
            raise ValueError("execution_order must contain each variant exactly once")
        return self


class VariantReference(FrozenModel):
    variant_id: UUID
    variant_sha256: HexDigest
    manifest_path: str = Field(min_length=1)


class ExperimentManifest(FrozenModel):
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    code_revision: str = Field(min_length=1)
    dataset_version_id: UUID
    dataset_sha256: HexDigest
    protocol_snapshot: Dict[str, JsonValue]
    statistics_plan: Dict[str, JsonValue]
    budget_snapshot: Dict[str, JsonValue]
    variants: Tuple[VariantReference, ...] = Field(min_length=2)
    status: ExperimentStatus = ExperimentStatus.DRAFT

    @model_validator(mode="after")
    def references_must_be_unique(self) -> "ExperimentManifest":
        ids = [reference.variant_id for reference in self.variants]
        hashes = [reference.variant_sha256 for reference in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("variant references must have unique IDs")
        if len(hashes) != len(set(hashes)):
            raise ValueError("variant references must have unique fingerprints")
        return self
