"""Strict configuration and validation dataset for benchmark-guided Skill search."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchSpecError(ValueError):
    """Raised when a search spec or search-only dataset is unsafe or incomplete."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MutationSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    hypothesis: str = Field(min_length=10)
    instruction: str = Field(min_length=10)


class SearchAlgorithmSpec(StrictModel):
    algorithm: Literal["successive_halving"] = "successive_halving"
    subset_size: int = Field(ge=1)
    promote_search_candidates: int = Field(default=2, ge=1)
    random_seed: int = 2026


class SearchConstraintSpec(StrictModel):
    max_skill_bytes: int = Field(default=20_000, ge=100)
    max_loss_cases: int = Field(default=0, ge=0)
    max_token_overhead_ratio: float = Field(default=0.25, ge=0)


class SearchBudgetSpec(StrictModel):
    max_candidate_case_evaluations: int = Field(ge=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class EvaluatorSpec(StrictModel):
    type: Literal["simulated_keyword", "process"]
    command: Tuple[str, ...] = ()
    version: str = Field(min_length=1)
    simulated: bool

    @model_validator(mode="after")
    def process_requires_command(self) -> "EvaluatorSpec":
        if self.type == "process" and not self.command:
            raise ValueError("process evaluator requires command")
        if self.type == "simulated_keyword" and self.command:
            raise ValueError("simulated evaluator cannot declare a command")
        if self.type == "simulated_keyword" and not self.simulated:
            raise ValueError("simulated_keyword evaluator must declare simulated=true")
        return self


class OptimizationSearchSpec(StrictModel):
    schema_version: Literal["ase/optimization-search/v1alpha1"]
    name: str = Field(min_length=1)
    base_skill_path: Path
    manual_skill_path: Path
    validation_search_path: Path
    mutations: Tuple[MutationSpec, ...] = Field(min_length=3)
    search: SearchAlgorithmSpec
    constraints: SearchConstraintSpec = SearchConstraintSpec()
    budget: SearchBudgetSpec
    evaluator: EvaluatorSpec

    @model_validator(mode="after")
    def mutation_ids_are_unique(self) -> "OptimizationSearchSpec":
        ids = [item.id for item in self.mutations]
        if len(ids) != len(set(ids)):
            raise ValueError("mutation ids must be unique")
        if self.search.promote_search_candidates > len(ids):
            raise ValueError("promotion width exceeds search candidate count")
        return self

    @classmethod
    def load(cls, path: Path) -> "OptimizationSearchSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise SearchSpecError(f"invalid optimization spec {path}: {exc}") from exc


class SearchCase(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    required_terms: Tuple[str, ...] = ()
    leakage_tokens: Tuple[str, ...] = ()


class ValidationSearchDataset(StrictModel):
    schema_version: Literal["ase/optimizer-validation/v1alpha1"]
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    split: Literal["validation_search"]
    simulated: bool
    cases: Tuple[SearchCase, ...] = Field(min_length=2)
    source_dataset: Optional[str] = None

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> "ValidationSearchDataset":
        ids = [item.id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("validation case ids must be unique")
        if self.simulated and any(not item.required_terms for item in self.cases):
            raise ValueError("simulated validation cases require required_terms")
        return self

    @classmethod
    def load(cls, path: Path) -> "ValidationSearchDataset":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise SearchSpecError(f"invalid validation_search dataset {path}: {exc}") from exc
