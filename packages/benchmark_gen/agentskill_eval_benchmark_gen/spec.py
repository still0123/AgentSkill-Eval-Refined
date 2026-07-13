"""Strict input specification for the local Git-history benchmark generator."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpecError(ValueError):
    """Raised when a benchmark generation specification is invalid."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateSpec(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    before_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    after_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    task: str = Field(min_length=20)
    issue_url: Optional[str] = None
    production_paths: Tuple[str, ...] = Field(min_length=1)
    regression_test_paths: Tuple[str, ...] = Field(min_length=1)
    test_command: Tuple[str, ...] = Field(min_length=1)
    alternative_patch: str = Field(min_length=1)
    category: str = "positive"
    tags: Tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def paths_are_safe(self) -> "CandidateSpec":
        for value in (*self.production_paths, *self.regression_test_paths):
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe repository path: {value}")
        if self.test_command[0] not in {"python", "python3", "${PYTHON}"}:
            raise ValueError("test_command must invoke the controlled Python interpreter")
        return self


class QualityGateSpec(StrictModel):
    repeat_count: int = Field(default=3, ge=3, le=10)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    mutation_required: bool = True
    alternative_required: bool = True
    prohibit_network_time_random: bool = True
    prohibit_patch_leakage: bool = True
    max_repository_files: int = Field(default=5000, ge=1)
    max_repository_bytes: int = Field(default=50_000_000, ge=1)

    @model_validator(mode="after")
    def mandatory_mvp_gates_cannot_be_disabled(self) -> "QualityGateSpec":
        required = (
            self.mutation_required,
            self.alternative_required,
            self.prohibit_network_time_random,
            self.prohibit_patch_leakage,
        )
        if not all(required):
            raise ValueError("mandatory MVP quality gates cannot be disabled")
        return self


class BudgetSpec(StrictModel):
    max_candidates: int = Field(default=20, ge=1)
    max_commands: int = Field(default=200, ge=1)
    wall_seconds: int = Field(default=7200, ge=1)


class BenchmarkGenerationSpec(StrictModel):
    schema_version: Literal["ase/benchmark-generation/v1alpha1"]
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    repository_path: Path
    repository_url: str = Field(min_length=1)
    fork_lineage: str = Field(min_length=1)
    license_spdx: str = Field(min_length=1)
    license_path: str = Field(min_length=1)
    target_split: Literal["validation_search", "validation_confirm", "locked_test"] = (
        "validation_search"
    )
    generator_profile: str = "local-git-history/v1"
    verifier_profile: str = "deterministic-subprocess/v1"
    contamination_risk: Literal["low", "medium", "high", "unknown"]
    quality_gate: QualityGateSpec = QualityGateSpec()
    budget: BudgetSpec = BudgetSpec()
    candidates: Tuple[CandidateSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def candidates_are_unique_and_bounded(self) -> "BenchmarkGenerationSpec":
        keys = [item.key for item in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("candidate keys must be unique")
        if len(keys) > self.budget.max_candidates:
            raise ValueError("candidate count exceeds max_candidates")
        license_path = Path(self.license_path)
        if license_path.is_absolute() or ".." in license_path.parts:
            raise ValueError("license_path must be a safe repository-relative path")
        return self

    @classmethod
    def load(cls, path: Path) -> "BenchmarkGenerationSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise SpecError(f"invalid benchmark generation spec {path}: {exc}") from exc
