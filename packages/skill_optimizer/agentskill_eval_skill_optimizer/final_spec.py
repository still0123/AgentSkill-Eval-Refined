"""Strict inputs for independent confirmation and locked-test evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Tuple
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentskill_eval_skill_optimizer.spec import EvaluatorSpec


class FinalSpecError(ValueError):
    """Raised when a final-evaluation input is incomplete or unsafe."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalGateSpec(StrictModel):
    min_absolute_gain: float = Field(default=0.01, ge=0, le=1)
    max_loss_cases: int = Field(default=0, ge=0)
    max_token_overhead_ratio: float = Field(default=0.25, ge=0)
    min_independent_groups: int = Field(default=2, ge=1)
    bootstrap_resamples: int = Field(default=2_000, ge=100, le=100_000)
    bootstrap_seed: int = 2026


class IndependentFinalEvaluationSpec(StrictModel):
    schema_version: Literal["ase/independent-final-evaluation/v1alpha1"]
    name: str = Field(min_length=1)
    optimization_job_id: UUID
    dataset_path: Path
    stage: Literal["validation_confirm", "locked_test"]
    repeats: int = Field(default=3, ge=1, le=20)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    gates: FinalGateSpec = FinalGateSpec()
    evaluator: EvaluatorSpec

    @classmethod
    def load(cls, path: Path) -> "IndependentFinalEvaluationSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise FinalSpecError(f"invalid final evaluation spec {path}: {exc}") from exc


class FinalEvaluationCase(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    independence_group: str = Field(min_length=1)
    required_terms: Tuple[str, ...] = Field(min_length=1)


class SimulatedFinalDataset(StrictModel):
    schema_version: Literal["ase/final-evaluation-dataset/v1alpha1"]
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    split: Literal["validation_confirm", "locked_test"]
    simulated: Literal[True]
    cases: Tuple[FinalEvaluationCase, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "SimulatedFinalDataset":
        ids = [item.id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("final evaluation case ids must be unique")
        return self

    @classmethod
    def load(cls, path: Path) -> "SimulatedFinalDataset":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise FinalSpecError(f"invalid simulated final dataset {path}: {exc}") from exc
