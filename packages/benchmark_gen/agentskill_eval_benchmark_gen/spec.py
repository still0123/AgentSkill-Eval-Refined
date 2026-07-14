"""Strict input specification for the local Git-history benchmark generator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpecError(ValueError):
    """Raised when a benchmark generation specification is invalid."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateSpec(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    source_key: str = Field(default="primary", pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    provenance_family: Optional[str] = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9-]{2,119}$"
    )
    before_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    after_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    task: str = Field(min_length=20)
    issue_url: Optional[str] = None
    production_paths: Tuple[str, ...] = Field(min_length=1)
    regression_test_paths: Tuple[str, ...] = Field(min_length=1)
    test_command: Tuple[str, ...] = Field(min_length=1)
    alternative_patch: str = Field(min_length=1)
    category: Literal["positive", "negative", "distractor", "complex", "robustness"] = (
        "positive"
    )
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


class RepositorySourceSpec(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    repository_path: Path
    repository_url: str = Field(min_length=1)
    fork_lineage: str = Field(min_length=1)
    license_spdx: str = Field(min_length=1)
    license_path: str = Field(min_length=1)
    contamination_risk: Literal["low", "medium", "high", "unknown"]

    @model_validator(mode="after")
    def license_path_is_safe(self) -> "RepositorySourceSpec":
        path = Path(self.license_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("license_path must be a safe repository-relative path")
        return self


class BenchmarkGenerationSpec(StrictModel):
    schema_version: Literal[
        "ase/benchmark-generation/v1alpha1", "ase/benchmark-generation/v1alpha2"
    ]
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    repository_path: Optional[Path] = None
    repository_url: Optional[str] = Field(default=None, min_length=1)
    fork_lineage: Optional[str] = Field(default=None, min_length=1)
    license_spdx: Optional[str] = Field(default=None, min_length=1)
    license_path: Optional[str] = Field(default=None, min_length=1)
    sources: Tuple[RepositorySourceSpec, ...] = ()
    target_split: Literal[
        "train",
        "validation_search",
        "regression_dev",
        "validation_confirm",
        "locked_test",
    ] = "validation_search"
    split_plan_required: bool = False
    split_plan_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    generator_profile: str = "local-git-history/v1"
    verifier_profile: str = "deterministic-subprocess/v1"
    contamination_risk: Optional[Literal["low", "medium", "high", "unknown"]] = None
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
        if self.split_plan_required and self.split_plan_sha256 is not None:
            raise ValueError("split-plan source catalog cannot claim a derived plan hash")
        if self.sources:
            if self.schema_version != "ase/benchmark-generation/v1alpha2":
                raise ValueError("multi-source specs require schema v1alpha2")
            legacy = (
                self.repository_path,
                self.repository_url,
                self.fork_lineage,
                self.license_spdx,
                self.license_path,
                self.contamination_risk,
            )
            if any(value is not None for value in legacy):
                raise ValueError("multi-source specs cannot mix legacy repository fields")
            source_keys = [source.key for source in self.sources]
            lineages = [source.fork_lineage for source in self.sources]
            if len(source_keys) != len(set(source_keys)):
                raise ValueError("repository source keys must be unique")
            if len(lineages) != len(set(lineages)):
                raise ValueError("repository fork lineages must be unique")
            known = set(source_keys)
            for candidate in self.candidates:
                if candidate.source_key not in known:
                    raise ValueError(f"unknown candidate source_key: {candidate.source_key}")
                if candidate.provenance_family is None:
                    raise ValueError("multi-source candidates require provenance_family")
        else:
            if self.schema_version != "ase/benchmark-generation/v1alpha1":
                raise ValueError("schema v1alpha2 requires sources")
            legacy = (
                self.repository_path,
                self.repository_url,
                self.fork_lineage,
                self.license_spdx,
                self.license_path,
                self.contamination_risk,
            )
            if any(value is None for value in legacy):
                raise ValueError("legacy single-source repository fields are required")
            assert self.license_path is not None
            license_path = Path(self.license_path)
            if license_path.is_absolute() or ".." in license_path.parts:
                raise ValueError("license_path must be a safe repository-relative path")
            if any(candidate.source_key != "primary" for candidate in self.candidates):
                raise ValueError("legacy candidates must use source_key=primary")
        return self

    def repository_sources(self) -> Tuple[RepositorySourceSpec, ...]:
        if self.sources:
            return self.sources
        assert self.repository_path is not None
        assert self.repository_url is not None
        assert self.fork_lineage is not None
        assert self.license_spdx is not None
        assert self.license_path is not None
        assert self.contamination_risk is not None
        return (
            RepositorySourceSpec(
                key="primary",
                repository_path=self.repository_path,
                repository_url=self.repository_url,
                fork_lineage=self.fork_lineage,
                license_spdx=self.license_spdx,
                license_path=self.license_path,
                contamination_risk=self.contamination_risk,
            ),
        )

    def semantic_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = self.model_dump(mode="json")
        payload.pop("repository_path", None)
        for source in payload.get("sources", []):
            source.pop("repository_path", None)
        return payload

    @classmethod
    def load(cls, path: Path) -> "BenchmarkGenerationSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise SpecError(f"invalid benchmark generation spec {path}: {exc}") from exc
