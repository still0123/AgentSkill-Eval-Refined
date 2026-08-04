"""Immutable five-way benchmark split publication for Skill optimization."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Dict, Literal, Mapping, Tuple
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
from agentskill_eval_benchmark_gen.spec import BenchmarkGenerationSpec
from agentskill_eval_benchmark_gen.split_audit import audit_loaded_datasets, split_inventory
from agentskill_eval_contracts import BenchmarkCandidateStatus, ReviewDecision, stable_sha256
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter

REQUIRED_SPLITS = (
    DatasetSplit.TRAIN,
    DatasetSplit.VALIDATION_SEARCH,
    DatasetSplit.REGRESSION_DEV,
    DatasetSplit.VALIDATION_CONFIRM,
    DatasetSplit.LOCKED_TEST,
)
OPTIMIZER_VISIBLE_SPLITS = frozenset(
    {
        DatasetSplit.TRAIN,
        DatasetSplit.VALIDATION_SEARCH,
        DatasetSplit.REGRESSION_DEV,
    }
)


class OptimizationSplitError(RuntimeError):
    """Raised when the optimization benchmark cannot be safely published."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceBundle(StrictModel):
    source_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OptimizationSplitInput(StrictModel):
    split: DatasetSplit
    source_spec: Path
    candidate_keys: Tuple[str, ...] = Field(min_length=1, max_length=4)
    bundles: Tuple[SourceBundle, ...] = Field(min_length=1)
    optimizer_visible: bool

    @model_validator(mode="after")
    def visibility_matches_stage_boundary(self) -> "OptimizationSplitInput":
        expected = self.split in OPTIMIZER_VISIBLE_SPLITS
        if self.optimizer_visible != expected:
            raise ValueError(
                f"optimizer_visible must be {expected} for split {self.split.value}"
            )
        if len(set(self.candidate_keys)) != len(self.candidate_keys):
            raise ValueError("candidate keys must be unique inside one split")
        source_keys = [item.source_key for item in self.bundles]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("bundle source keys must be unique inside one split")
        return self


class OptimizationBenchmarkPlan(StrictModel):
    schema_version: Literal["ase/optimization-benchmark-plan/v1"] = (
        "ase/optimization-benchmark-plan/v1"
    )
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1)
    claim_limit: str = Field(min_length=1)
    splits: Tuple[OptimizationSplitInput, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def contains_exactly_five_isolated_split_inputs(self) -> "OptimizationBenchmarkPlan":
        split_values = tuple(item.split for item in self.splits)
        if set(split_values) != set(REQUIRED_SPLITS) or len(set(split_values)) != 5:
            raise ValueError("plan requires train/search/regression/confirm/locked exactly once")
        keys = [key for item in self.splits for key in item.candidate_keys]
        if len(keys) < 5 or len(keys) > 20 or len(set(keys)) != len(keys):
            raise ValueError("plan requires five to twenty globally unique candidate keys")
        return self

    @classmethod
    def load(cls, path: Path) -> "OptimizationBenchmarkPlan":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise OptimizationSplitError(f"invalid optimization split plan {path}: {exc}") from exc


class SplitDatasetReference(StrictModel):
    split: DatasetSplit
    dataset_version_id: UUID
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str = Field(min_length=1)
    case_count: int = Field(ge=1)
    candidate_keys: Tuple[str, ...]
    source_lineages: Tuple[str, ...]
    independence_groups: Tuple[str, ...]
    command_evidence_count: int = Field(ge=0)


class OptimizationBenchmarkRelease(StrictModel):
    schema_version: Literal["ase/optimization-benchmark-release/v1"] = (
        "ase/optimization-benchmark-release/v1"
    )
    name: str
    version: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str
    verifier_version: str
    total_case_count: int = Field(ge=1)
    repository_count: int = Field(ge=1)
    independence_group_count: int = Field(ge=1)
    splits: Tuple[SplitDatasetReference, ...]
    locked_policy: Literal["withheld_until_one_shot_final_evaluation"]
    claim_limit: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @staticmethod
    def calculate_content_sha256(payload: Mapping[str, object]) -> str:
        material = dict(payload)
        material.pop("content_sha256", None)
        return stable_sha256(material)

    @model_validator(mode="after")
    def immutable_content_hash_and_shape_match(self) -> "OptimizationBenchmarkRelease":
        payload = self.model_dump(mode="json")
        expected = self.calculate_content_sha256(payload)
        if self.content_sha256 != expected:
            raise ValueError("optimization benchmark release content hash mismatch")
        if self.total_case_count != sum(item.case_count for item in self.splits):
            raise ValueError("release total_case_count does not match split references")
        if len(self.splits) != 5:
            raise ValueError("release must contain exactly five DatasetVersions")
        if {item.split for item in self.splits} != set(REQUIRED_SPLITS):
            raise ValueError("release split set is incomplete")
        if any(item.case_count < 1 or item.case_count > 4 for item in self.splits):
            raise ValueError("each released DatasetVersion must contain one to four cases")
        return self


class OptimizationBenchmarkPublisher:
    """Reconstruct, audit, and publish one immutable five-way benchmark release."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()
        self.generator = AutomaticBenchmarkGenerator(self.workspace)

    def validate_plan(
        self, plan: OptimizationBenchmarkPlan, plan_path: Path
    ) -> Tuple[BenchmarkGenerationSpec, ...]:
        # The source files are intentionally non-executable catalogs.  Only this complete,
        # five-way allocation may turn them into generator inputs.  Freeze the common plan
        # lineage into every selected spec so downstream promotion/final-evaluation code can
        # prove that all DatasetVersions came from the same audited allocation.
        split_plan_sha256 = self._plan_sha256(plan, plan_path)
        specs = tuple(
            self._selected_spec(item, plan_path).model_copy(
                update={
                    "split_plan_required": False,
                    "split_plan_sha256": split_plan_sha256,
                }
            )
            for item in plan.splits
        )
        repositories: Dict[object, DatasetSplit] = {}
        lineages: Dict[object, DatasetSplit] = {}
        families: Dict[object, DatasetSplit] = {}
        for split_input, spec in zip(plan.splits, specs):
            used_sources = {candidate.source_key for candidate in spec.candidates}
            source_by_key = {source.key: source for source in spec.repository_sources()}
            for source_key in used_sources:
                source = source_by_key[source_key]
                self._claim_unique(
                    repositories, source.repository_url, split_input.split, "repository"
                )
                self._claim_unique(lineages, source.fork_lineage, split_input.split, "fork lineage")
            for candidate in spec.candidates:
                source = source_by_key[candidate.source_key]
                family = candidate.provenance_family or candidate.after_commit
                self._claim_unique(
                    families,
                    (source.fork_lineage, family),
                    split_input.split,
                    "patch family",
                )
        if len(repositories) != 5:
            raise OptimizationSplitError(
                f"plan requires five independent repositories, found {len(repositories)}"
            )
        if len(families) < len(plan.splits):
            raise OptimizationSplitError(
                f"plan requires at least five independent patch families, found {len(families)}"
            )
        return specs

    def publish(
        self,
        plan: OptimizationBenchmarkPlan,
        plan_path: Path,
        *,
        reviewer: str,
        publisher: str,
    ) -> Tuple[OptimizationBenchmarkRelease, Path]:
        release_dir = (
            self.workspace / "optimization-benchmark-releases" / plan.name / plan.version
        )
        release_path = release_dir / "release-manifest.json"
        if release_path.exists():
            release = self.load_release(release_path)
            self.verify(release)
            return release, release_dir

        specs = self.validate_plan(plan, plan_path)
        references = []
        for split_input, template in zip(plan.splits, specs):
            spec = self._materialize_sources(split_input, template, plan_path)
            result = self.generator.generate(spec)
            rejected = [
                item
                for item in result.candidates
                if item.status != BenchmarkCandidateStatus.DEDUPED
            ]
            if rejected:
                details = "; ".join(
                    f"{item.key}: {', '.join(item.rejection_reasons)}" for item in rejected
                )
                raise OptimizationSplitError(f"candidate verification failed: {details}")
            for candidate in result.candidates:
                self.generator.review(
                    result.job.id,
                    candidate.id,
                    reviewer,
                    ReviewDecision.APPROVED,
                    (
                        "offline evidence, provenance, license, mutation, and "
                        "alternative repair reviewed"
                    ),
                )
            version, destination = self.generator.publish(result.job.id, publisher)
            loaded = DatasetLoader().load(destination)
            references.append(
                SplitDatasetReference(
                    split=split_input.split,
                    dataset_version_id=version.id,
                    dataset_content_sha256=version.content_sha256,
                    relative_path=destination.relative_to(self.workspace).as_posix(),
                    case_count=len(version.cases),
                    candidate_keys=tuple(sorted(item.key for item in result.candidates)),
                    source_lineages=version.source_lineages,
                    independence_groups=loaded.independence_groups,
                    command_evidence_count=sum(
                        len(item.command_evidence) for item in result.candidates
                    ),
                )
            )

        plan_sha = self._plan_sha256(plan, plan_path)
        base: Dict[str, object] = {
            "schema_version": "ase/optimization-benchmark-release/v1",
            "name": plan.name,
            "version": plan.version,
            "plan_sha256": plan_sha,
            "generator_version": GENERATOR_VERSION,
            "verifier_version": VERIFIER_VERSION,
            "total_case_count": sum(item.case_count for item in references),
            "repository_count": len(
                {lineage for item in references for lineage in item.source_lineages}
            ),
            "independence_group_count": len(
                {group for item in references for group in item.independence_groups}
            ),
            "splits": [item.model_dump(mode="json") for item in references],
            "locked_policy": "withheld_until_one_shot_final_evaluation",
            "claim_limit": plan.claim_limit,
        }
        release = OptimizationBenchmarkRelease.model_validate(
            {**base, "content_sha256": stable_sha256(base)}
        )
        release_dir.mkdir(parents=True, exist_ok=False)
        self.writer.write(
            release_path,
            json.dumps(
                release.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8"),
        )
        self.writer.write(
            release_dir / "optimizer-view.json",
            json.dumps(
                self.optimizer_view(release), ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8"),
        )
        self.verify(release)
        return release, release_dir

    def verify(self, release: OptimizationBenchmarkRelease) -> None:
        loaded = []
        for reference in release.splits:
            root = (self.workspace / reference.relative_path).resolve(strict=True)
            if not root.is_relative_to(self.workspace):
                raise OptimizationSplitError("DatasetVersion path escapes workspace")
            dataset = DatasetLoader().load(root)
            store_version = next(
                (
                    version
                    for version, path in BenchmarkStore(self.workspace).published_dataset_versions()
                    if path == root
                ),
                None,
            )
            if store_version is None:
                raise OptimizationSplitError("DatasetVersion is not registered in benchmark store")
            if (
                store_version.id != reference.dataset_version_id
                or store_version.content_sha256 != reference.dataset_content_sha256
                or len(dataset.cases) != reference.case_count
                or dataset.independence_groups != reference.independence_groups
            ):
                raise OptimizationSplitError("released DatasetVersion reference mismatch")
            loaded.append(dataset)
        audit = audit_loaded_datasets(loaded)
        try:
            audit.require_passed()
        except ValueError as exc:
            raise OptimizationSplitError(str(exc)) from exc
        inventory = split_inventory(audit.entries)
        expected = {item.split: item.case_count for item in release.splits}
        if any(inventory.get(split, 0) != expected[split] for split in REQUIRED_SPLITS):
            raise OptimizationSplitError("released split inventory does not match references")

    @staticmethod
    def optimizer_view(release: OptimizationBenchmarkRelease) -> Mapping[str, object]:
        visible = []
        withheld = []
        for item in release.splits:
            if item.split in OPTIMIZER_VISIBLE_SPLITS:
                visible.append(item.model_dump(mode="json"))
            else:
                withheld.append(
                    {
                        "split": item.split.value,
                        "case_count": item.case_count,
                        "receipt_sha256": stable_sha256(
                            {
                                "release": release.content_sha256,
                                "split": item.split.value,
                                "dataset": item.dataset_content_sha256,
                            }
                        ),
                    }
                )
        return {
            "schema_version": "ase/optimization-benchmark-optimizer-view/v1",
            "release_sha256": release.content_sha256,
            "visible_splits": visible,
            "withheld_splits": withheld,
            "locked_test_accessed": False,
            "policy": "validation_confirm and locked_test paths/cases are not optimizer inputs",
        }

    @staticmethod
    def load_release(path: Path) -> OptimizationBenchmarkRelease:
        try:
            payload = path.read_text(encoding="utf-8")
            return OptimizationBenchmarkRelease.model_validate_json(payload)
        except (OSError, ValueError) as exc:
            raise OptimizationSplitError(f"invalid release manifest {path}: {exc}") from exc

    def _selected_spec(
        self, split_input: OptimizationSplitInput, plan_path: Path
    ) -> BenchmarkGenerationSpec:
        source_path = self._resolve_input(plan_path, split_input.source_spec)
        template = BenchmarkGenerationSpec.load(source_path)
        candidates_by_key = {item.key: item for item in template.candidates}
        missing = sorted(set(split_input.candidate_keys) - set(candidates_by_key))
        if missing:
            raise OptimizationSplitError(
                f"split {split_input.split.value} references unknown candidates: {missing}"
            )
        candidates = tuple(candidates_by_key[key] for key in split_input.candidate_keys)
        used_keys = {candidate.source_key for candidate in candidates}
        if template.sources:
            sources = tuple(source for source in template.sources if source.key in used_keys)
            return template.model_copy(
                update={
                    "name": f"{template.name}-{split_input.split.value}",
                    "target_split": split_input.split.value,
                    "sources": sources,
                    "candidates": candidates,
                    "budget": template.budget.model_copy(
                        update={
                            "max_candidates": len(candidates),
                            "max_commands": 12 * len(candidates),
                        }
                    ),
                }
            )
        return template.model_copy(
            update={
                "target_split": split_input.split.value,
                "candidates": candidates,
                "budget": template.budget.model_copy(
                    update={
                        "max_candidates": len(candidates),
                        "max_commands": 12 * len(candidates),
                    }
                ),
            }
        )

    def _materialize_sources(
        self,
        split_input: OptimizationSplitInput,
        spec: BenchmarkGenerationSpec,
        plan_path: Path,
    ) -> BenchmarkGenerationSpec:
        bundle_by_key = {item.source_key: item for item in split_input.bundles}
        required = {candidate.source_key for candidate in spec.candidates}
        if set(bundle_by_key) != required:
            raise OptimizationSplitError(
                f"bundle keys for {split_input.split.value} do not match selected sources"
            )
        paths: Dict[str, Path] = {}
        for source in spec.repository_sources():
            bundle = bundle_by_key[source.key]
            bundle_path = self._resolve_input(plan_path, bundle.path)
            if self._file_sha256(bundle_path) != bundle.sha256:
                raise OptimizationSplitError(f"bundle hash mismatch: {bundle_path}")
            destination = self.workspace / "benchmark-source-cache" / bundle.sha256 / source.key
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ("git", "clone", "--quiet", str(bundle_path), str(destination)),
                    check=True,
                )
                subprocess.run(
                    (
                        "git",
                        "-C",
                        str(destination),
                        "remote",
                        "set-url",
                        "origin",
                        source.repository_url,
                    ),
                    check=True,
                )
            paths[source.key] = destination
        if spec.sources:
            sources = tuple(
                source.model_copy(update={"repository_path": paths[source.key]})
                for source in spec.sources
            )
            return spec.model_copy(update={"sources": sources})
        return spec.model_copy(update={"repository_path": paths["primary"]})

    def _plan_sha256(self, plan: OptimizationBenchmarkPlan, plan_path: Path) -> str:
        split_material = []
        for item in plan.splits:
            source_spec = self._resolve_input(plan_path, item.source_spec)
            split_material.append(
                {
                    "split": item.model_dump(mode="json"),
                    "source_spec_sha256": self._file_sha256(source_spec),
                }
            )
        return stable_sha256(
            {"plan": plan.model_dump(mode="json"), "split_material": split_material}
        )

    @staticmethod
    def _claim_unique(
        seen: Dict[object, DatasetSplit],
        key: object,
        split: DatasetSplit,
        label: str,
    ) -> None:
        previous = seen.setdefault(key, split)
        if previous != split:
            raise OptimizationSplitError(
                f"{label} {key!r} crosses {previous.value} and {split.value}"
            )

    @staticmethod
    def _resolve_input(plan_path: Path, value: Path) -> Path:
        path = value if value.is_absolute() else plan_path.parent / value
        return path.resolve(strict=True)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
