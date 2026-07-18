"""Publish an independent development DatasetVersion before paid baseline screening."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Mapping, Tuple
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentskill_eval_benchmark_gen.dataset import DatasetLoader, DatasetSplit
from agentskill_eval_benchmark_gen.generator import (
    GENERATOR_VERSION,
    VERIFIER_VERSION,
    AutomaticBenchmarkGenerator,
    BenchmarkStore,
)
from agentskill_eval_benchmark_gen.optimization_split import (
    OptimizationBenchmarkPublisher,
    OptimizationBenchmarkRelease,
    OptimizationSplitInput,
    SourceBundle,
)
from agentskill_eval_benchmark_gen.spec import BenchmarkGenerationSpec
from agentskill_eval_contracts import BenchmarkCandidateStatus, ReviewDecision, stable_sha256
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter


class RegressionDevRevisionError(RuntimeError):
    """Raised when a development DatasetVersion revision is invalid."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegressionDevRevisionPlan(StrictModel):
    """One independent four-Case development dataset awaiting observed baseline screening."""

    schema_version: Literal["ase/regression-dev-revision-plan/v1alpha1"] = (
        "ase/regression-dev-revision-plan/v1alpha1"
    )
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    base_release_manifest: Path
    base_benchmark_workspace: Path
    source_spec: Path
    candidate_keys: Tuple[str, ...] = Field(min_length=4, max_length=4)
    bundles: Tuple[SourceBundle, ...] = Field(min_length=1)
    required_observed_baseline_failures: int = Field(default=1, ge=1, le=4)
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def candidates_are_unique(self) -> "RegressionDevRevisionPlan":
        if len(set(self.candidate_keys)) != 4:
            raise ValueError("regression_dev revision requires four unique candidate keys")
        return self

    @classmethod
    def load(cls, path: Path) -> "RegressionDevRevisionPlan":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            plan = cls.model_validate(payload)
            root = path.expanduser().resolve(strict=True).parent

            def resolved(value: Path, *, directory: bool = False) -> Path:
                target = value if value.is_absolute() else root / value
                target = target.expanduser().resolve(strict=True)
                if directory != target.is_dir():
                    kind = "directory" if directory else "file"
                    raise RegressionDevRevisionError(f"expected {kind}: {target}")
                return target

            return plan.model_copy(
                update={
                    "base_release_manifest": resolved(plan.base_release_manifest),
                    "base_benchmark_workspace": resolved(
                        plan.base_benchmark_workspace, directory=True
                    ),
                    "source_spec": resolved(plan.source_spec),
                    "bundles": tuple(
                        item.model_copy(update={"path": resolved(item.path)})
                        for item in plan.bundles
                    ),
                }
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise RegressionDevRevisionError(
                f"invalid regression_dev revision plan: {exc}"
            ) from exc


class RegressionDevCandidateRelease(StrictModel):
    schema_version: Literal["ase/regression-dev-candidate-release/v1alpha1"] = (
        "ase/regression-dev-candidate-release/v1alpha1"
    )
    name: str
    version: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_optimization_release_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_version_id: UUID
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_relative_path: str = Field(min_length=1)
    repository_lineages: Tuple[str, ...] = Field(min_length=1)
    independence_groups: Tuple[str, ...] = Field(min_length=4)
    case_ids: Tuple[str, ...] = Field(min_length=4, max_length=4)
    generator_version: str
    verifier_version: str
    required_observed_baseline_failures: int = Field(ge=1, le=4)
    status: Literal["AWAITING_OBSERVED_BASELINE_SCREENING"] = (
        "AWAITING_OBSERVED_BASELINE_SCREENING"
    )
    simulated: Literal[False] = False
    validation_confirm_accessed: Literal[False] = False
    locked_test_accessed: Literal[False] = False
    claim_limit: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @staticmethod
    def calculate_content_sha256(payload: Mapping[str, object]) -> str:
        material = dict(payload)
        material.pop("content_sha256", None)
        return stable_sha256(material)

    @model_validator(mode="after")
    def content_hash_and_shape_match(self) -> "RegressionDevCandidateRelease":
        if self.content_sha256 != self.calculate_content_sha256(
            self.model_dump(mode="json")
        ):
            raise ValueError("regression_dev candidate release content hash mismatch")
        if len(set(self.case_ids)) != 4 or len(set(self.independence_groups)) != 4:
            raise ValueError("regression_dev candidate release requires four independent cases")
        return self


class RegressionDevRevisionPublisher:
    """Publish one offline candidate dataset without touching confirmation or locked data."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.writer = AtomicFileWriter()

    def validate_plan(
        self, plan: RegressionDevRevisionPlan, plan_path: Path
    ) -> tuple[OptimizationBenchmarkRelease, BenchmarkGenerationSpec]:
        base_publisher = OptimizationBenchmarkPublisher(plan.base_benchmark_workspace)
        base = base_publisher.load_release(plan.base_release_manifest)
        base_publisher.verify(base)

        split_input = OptimizationSplitInput(
            split=DatasetSplit.REGRESSION_DEV,
            source_spec=plan.source_spec,
            candidate_keys=plan.candidate_keys,
            bundles=plan.bundles,
            optimizer_visible=True,
        )
        helper = OptimizationBenchmarkPublisher(self.workspace)
        spec = helper._selected_spec(split_input, plan_path)  # noqa: SLF001
        old_lineages = {lineage for item in base.splits for lineage in item.source_lineages}
        new_lineages = {item.fork_lineage for item in spec.repository_sources()}
        overlap = sorted(old_lineages & new_lineages)
        if overlap:
            raise RegressionDevRevisionError(
                "candidate repository overlaps frozen optimization release: " + ", ".join(overlap)
            )
        materialized = helper._materialize_sources(  # noqa: SLF001
            split_input, spec, plan_path
        ).model_copy(
            update={
                "name": plan.name,
                "version": plan.version,
                "target_split": DatasetSplit.REGRESSION_DEV.value,
            }
        )
        return base, materialized

    def publish(
        self,
        plan: RegressionDevRevisionPlan,
        plan_path: Path,
        *,
        reviewer: str,
        publisher: str,
    ) -> tuple[RegressionDevCandidateRelease, Path]:
        release_dir = (
            self.workspace / "regression-dev-candidate-releases" / plan.name / plan.version
        )
        release_path = release_dir / "release-manifest.json"
        if release_path.exists():
            release = self.load_release(release_path)
            self.verify(release, plan)
            return release, release_dir

        base, spec = self.validate_plan(plan, plan_path)
        generator = AutomaticBenchmarkGenerator(self.workspace)
        result = generator.generate(spec)
        rejected = [
            item for item in result.candidates if item.status != BenchmarkCandidateStatus.DEDUPED
        ]
        if rejected:
            detail = "; ".join(
                f"{item.key}: {', '.join(item.rejection_reasons)}" for item in rejected
            )
            raise RegressionDevRevisionError(f"candidate verification failed: {detail}")
        for candidate in result.candidates:
            generator.review(
                result.job.id,
                candidate.id,
                reviewer,
                ReviewDecision.APPROVED,
                "offline reconstruction, license, oracle, mutation, and alternative reviewed",
            )
        version, dataset_path = generator.publish(result.job.id, publisher)
        loaded = DatasetLoader().load(dataset_path)
        plan_sha = self._plan_sha256(plan, plan_path)
        payload: dict[str, object] = {
            "schema_version": "ase/regression-dev-candidate-release/v1alpha1",
            "name": plan.name,
            "version": plan.version,
            "plan_sha256": plan_sha,
            "base_optimization_release_sha256": base.content_sha256,
            "dataset_version_id": str(version.id),
            "dataset_content_sha256": version.content_sha256,
            "dataset_relative_path": dataset_path.relative_to(self.workspace).as_posix(),
            "repository_lineages": list(version.source_lineages),
            "independence_groups": list(loaded.independence_groups),
            "case_ids": [item.metadata.case_id for item in loaded.cases],
            "generator_version": GENERATOR_VERSION,
            "verifier_version": VERIFIER_VERSION,
            "required_observed_baseline_failures": plan.required_observed_baseline_failures,
            "status": "AWAITING_OBSERVED_BASELINE_SCREENING",
            "simulated": False,
            "validation_confirm_accessed": False,
            "locked_test_accessed": False,
            "claim_limit": plan.claim_limit,
        }
        release = RegressionDevCandidateRelease.model_validate(
            {**payload, "content_sha256": stable_sha256(payload)}
        )
        release_dir.mkdir(parents=True, exist_ok=False)
        self.writer.write(
            release_path,
            json.dumps(
                release.model_dump(mode="json"), indent=2, sort_keys=True
            ).encode()
            + b"\n",
        )
        self.verify(release, plan)
        return release, release_dir

    def verify(
        self, release: RegressionDevCandidateRelease, plan: RegressionDevRevisionPlan
    ) -> None:
        base = OptimizationBenchmarkPublisher.load_release(plan.base_release_manifest)
        OptimizationBenchmarkPublisher(plan.base_benchmark_workspace).verify(base)
        if base.content_sha256 != release.base_optimization_release_sha256:
            raise RegressionDevRevisionError("base optimization release hash mismatch")
        dataset_path = (self.workspace / release.dataset_relative_path).resolve(strict=True)
        if not dataset_path.is_relative_to(self.workspace):
            raise RegressionDevRevisionError("candidate DatasetVersion path escapes workspace")
        loaded = DatasetLoader().load(dataset_path)
        version = next(
            (
                item
                for item, path in BenchmarkStore(self.workspace).published_dataset_versions()
                if path == dataset_path
            ),
            None,
        )
        if version is None or version.id != release.dataset_version_id:
            raise RegressionDevRevisionError("candidate DatasetVersion is not registered")
        if (
            version.content_sha256 != release.dataset_content_sha256
            or tuple(item.metadata.case_id for item in loaded.cases) != release.case_ids
            or loaded.independence_groups != release.independence_groups
            or any(item.metadata.split != DatasetSplit.REGRESSION_DEV for item in loaded.cases)
        ):
            raise RegressionDevRevisionError("candidate DatasetVersion reference mismatch")
        old_lineages = {lineage for item in base.splits for lineage in item.source_lineages}
        if old_lineages & set(release.repository_lineages):
            raise RegressionDevRevisionError(
                "candidate DatasetVersion is not repository-independent"
            )

    @staticmethod
    def load_release(path: Path) -> RegressionDevCandidateRelease:
        try:
            return RegressionDevCandidateRelease.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RegressionDevRevisionError(f"invalid candidate release: {exc}") from exc

    @staticmethod
    def _plan_sha256(plan: RegressionDevRevisionPlan, plan_path: Path) -> str:
        spec_bytes = plan.source_spec.read_bytes()
        return stable_sha256(
            {
                "plan": plan.model_dump(mode="json"),
                "source_spec_sha256": stable_sha256(spec_bytes.hex()),
                "bundle_sha256": [item.sha256 for item in plan.bundles],
                "plan_path_name": plan_path.name,
            }
        )
