"""Strict ingestion for curated Agent Skill evaluation datasets."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentskill_eval_contracts import BenchmarkDatasetVersion, stable_sha256
from agentskill_eval_experiment import CaseExecutionSpec
from agentskill_eval_experiment.storage.errors import IntegrityError
from agentskill_eval_experiment.storage.manifests import load_model


class DatasetError(ValueError):
    """Raised when a curated dataset is incomplete, unsafe, or inconsistent."""


class DatasetSplit(str, Enum):
    TRAIN = "train"
    REGRESSION_DEV = "regression_dev"
    CHALLENGE = "challenge"
    VALIDATION_SEARCH = "validation_search"
    VALIDATION_CONFIRM = "validation_confirm"
    LOCKED_TEST = "locked_test"


class CaseCategory(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    DISTRACTOR = "distractor"
    COMPLEX = "complex"
    ROBUSTNESS = "robustness"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetManifest(StrictModel):
    schema_version: str = Field(pattern=r"^ase-dataset/v1alpha1$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    license: str = Field(min_length=1)
    runner_name: str = "skill-up"
    runner_version: str = "0.5.0"
    demo_only: bool
    expected_case_count: int = Field(ge=1)
    case_metadata: Tuple[str, ...] = Field(min_length=1)
    minimum_category_counts: Dict[CaseCategory, int]

    @model_validator(mode="after")
    def references_are_unique_and_complete(self) -> "DatasetManifest":
        if len(set(self.case_metadata)) != len(self.case_metadata):
            raise ValueError("case_metadata paths must be unique")
        if len(self.case_metadata) != self.expected_case_count:
            raise ValueError("expected_case_count must match case_metadata length")
        return self


class CaseGroupKeys(StrictModel):
    independence_group: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=1, max_length=200)
    fork_lineage: str = Field(min_length=1, max_length=200)
    patch_family: str = Field(min_length=1, max_length=200)


class CaseProvenance(StrictModel):
    source_type: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    license: str = Field(min_length=1)
    contamination_risk: str = Field(pattern=r"^(low|medium|high)$")
    synthetic: bool


class CaseOracle(StrictModel):
    kind: str = Field(pattern=r"^(expect|rule_based|script)$")
    expected_signal: str = Field(min_length=1)


class CaseMetadata(StrictModel):
    schema_version: str = Field(pattern=r"^ase-case-meta/v1alpha1$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    case_ref: str = Field(min_length=1)
    split: DatasetSplit
    category: CaseCategory
    skill_applicable: bool
    group_keys: CaseGroupKeys
    provenance: CaseProvenance
    oracle: CaseOracle
    tags: Tuple[str, ...] = Field(min_length=1)


@dataclass(frozen=True)
class LoadedCase:
    metadata: CaseMetadata
    case_file: Path
    case_payload: Mapping[str, Any]
    fixture_path: Optional[Path]
    case_sha256: str
    grader_sha256: str
    prompt_sha256: str

    def execution_spec(self, dataset_id: UUID, eval_dir: Path) -> CaseExecutionSpec:
        return CaseExecutionSpec(
            id=uuid5(dataset_id, f"case:{self.metadata.case_id}"),
            runner_case_id=self.metadata.case_id,
            independence_group=self.metadata.group_keys.independence_group,
            source_eval_dir=eval_dir,
            case_file=self.case_file,
            case_sha256=self.case_sha256,
            grader_sha256=self.grader_sha256,
            platform_compiled_prompt_sha256=self.prompt_sha256,
        )


@dataclass(frozen=True)
class LoadedDataset:
    root: Path
    manifest: DatasetManifest
    cases: Tuple[LoadedCase, ...]
    dataset_sha256: str
    dataset_id: UUID
    dataset_version: Optional[BenchmarkDatasetVersion] = None

    @property
    def category_counts(self) -> Mapping[CaseCategory, int]:
        return Counter(item.metadata.category for item in self.cases)

    @property
    def independence_groups(self) -> Tuple[str, ...]:
        return tuple(sorted({item.metadata.group_keys.independence_group for item in self.cases}))

    def execution_specs(self) -> Tuple[CaseExecutionSpec, ...]:
        eval_dir = self.root / "evals"
        return tuple(item.execution_spec(self.dataset_id, eval_dir) for item in self.cases)


class DatasetLoader:
    """Load platform sidecars while leaving upstream Case semantics to skill-up."""

    def load(self, root: Path) -> LoadedDataset:
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise DatasetError("dataset root must be a directory")
        manifest_path = self._safe_path(root, "dataset.yaml", file=True)
        manifest = DatasetManifest.model_validate(self._read_yaml_mapping(manifest_path))
        cases = tuple(self._load_case(root, path) for path in manifest.case_metadata)
        ids = [item.metadata.case_id for item in cases]
        refs = [item.metadata.case_ref for item in cases]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise DatasetError("case IDs and case references must be unique")
        actual_counts = Counter(item.metadata.category for item in cases)
        for category, minimum in manifest.minimum_category_counts.items():
            if actual_counts[category] < minimum:
                raise DatasetError(
                    f"category {category.value} requires {minimum} cases, "
                    f"found {actual_counts[category]}"
                )
        dataset_version = self._validate_published_version(root, cases)
        digest = stable_sha256(
            {
                "manifest": manifest.model_dump(mode="json"),
                "cases": [
                    {
                        "metadata": item.metadata.model_dump(mode="json"),
                        "case_sha256": item.case_sha256,
                        "grader_sha256": item.grader_sha256,
                        "prompt_sha256": item.prompt_sha256,
                    }
                    for item in cases
                ],
            }
        )
        identity = f"agentskill-eval:{manifest.name}:{manifest.version}:{digest}"
        dataset_id = uuid5(NAMESPACE_URL, identity)
        return LoadedDataset(root, manifest, cases, digest, dataset_id, dataset_version)

    def _validate_published_version(
        self, root: Path, cases: Tuple[LoadedCase, ...]
    ) -> Optional[BenchmarkDatasetVersion]:
        version_path = root / "dataset-version.json"
        if not version_path.exists():
            return None
        try:
            version = load_model(version_path.read_bytes(), BenchmarkDatasetVersion)
        except (IntegrityError, ValueError) as exc:
            raise DatasetError(f"invalid DatasetVersion manifest: {exc}") from exc
        published = {item.case_id: item for item in version.cases}
        loaded = {item.metadata.case_id: item for item in cases}
        if set(published) != set(loaded):
            raise DatasetError("DatasetVersion case set does not match dataset metadata")
        splits = {item.metadata.split.value for item in cases}
        if splits != {version.split}:
            raise DatasetError("published DatasetVersion contains mixed or mismatched splits")
        lineages = tuple(
            sorted({item.metadata.group_keys.fork_lineage for item in cases})
        )
        if lineages != version.source_lineages:
            raise DatasetError("DatasetVersion source lineages do not match case metadata")
        for case_id, item in loaded.items():
            expected = published[case_id]
            grader_sha = self._script_hash(root, item.case_payload, case_id)
            actual = {
                "case": self._hash_file(item.case_file),
                "fixture": (
                    self._published_fixture_hash(item.fixture_path)
                    if item.fixture_path
                    else None
                ),
                "grader": grader_sha,
                "provenance": self._hash_file(
                    self._safe_path(root, f"provenance/{case_id}.json", file=True)
                ),
            }
            wanted = {
                "case": expected.case_sha256,
                "fixture": expected.fixture_sha256,
                "grader": expected.grader_sha256,
                "provenance": expected.provenance_sha256,
            }
            if expected.metadata_sha256 is not None:
                actual["metadata"] = self._hash_file(
                    self._safe_path(root, f"metadata/{case_id}.yaml", file=True)
                )
                wanted["metadata"] = expected.metadata_sha256
            if actual != wanted:
                raise DatasetError(f"published case integrity mismatch: {case_id}")
        return version

    def _load_case(self, root: Path, metadata_ref: str) -> LoadedCase:
        metadata_path = self._safe_path(root, metadata_ref, file=True)
        metadata = CaseMetadata.model_validate(self._read_yaml_mapping(metadata_path))
        case_file = self._safe_path(root, metadata.case_ref, file=True)
        payload = self._read_yaml_mapping(case_file)
        if payload.get("id") != metadata.case_id:
            raise DatasetError(f"case ID mismatch for {metadata.case_ref}")
        input_payload = payload.get("input")
        if not isinstance(input_payload, dict) or not isinstance(input_payload.get("prompt"), str):
            raise DatasetError(f"case {metadata.case_id} requires input.prompt")
        if "expect" not in payload and "judge" not in payload:
            raise DatasetError(f"case {metadata.case_id} requires deterministic grading")
        fixture_path = self._fixture_path(root, payload, metadata.case_id)
        fixture_sha = self._hash_tree(fixture_path) if fixture_path else None
        script_sha = self._script_hash(root, payload, metadata.case_id)
        grader = {"expect": payload.get("expect"), "judge": payload.get("judge")}
        if script_sha is not None:
            grader["script_sha256"] = script_sha
        return LoadedCase(
            metadata=metadata,
            case_file=case_file,
            case_payload=payload,
            fixture_path=fixture_path,
            case_sha256=stable_sha256(
                {"case_file_sha256": self._hash_file(case_file), "fixture_sha256": fixture_sha}
            ),
            grader_sha256=stable_sha256(grader),
            prompt_sha256=stable_sha256(input_payload),
        )

    def _fixture_path(
        self, root: Path, payload: Mapping[str, Any], case_id: str
    ) -> Optional[Path]:
        context = payload.get("context")
        if not isinstance(context, dict) or not context.get("repo_fixture"):
            return None
        ref = context["repo_fixture"]
        if not isinstance(ref, str):
            raise DatasetError(f"case {case_id} repo_fixture must be a string")
        return self._safe_path(root, ref, file=False)

    def _script_hash(
        self, root: Path, payload: Mapping[str, Any], case_id: str
    ) -> Optional[str]:
        judge = payload.get("judge")
        if not isinstance(judge, dict) or judge.get("type") != "script":
            return None
        ref = judge.get("script_path")
        if not isinstance(ref, str):
            raise DatasetError(f"case {case_id} script judge requires script_path")
        return self._hash_file(self._safe_path(root, ref, file=True))

    @staticmethod
    def _read_yaml_mapping(path: Path) -> Dict[str, Any]:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise DatasetError(f"cannot read YAML {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DatasetError(f"YAML root must be a mapping: {path}")
        return payload

    @staticmethod
    def _safe_path(root: Path, relative: str, *, file: bool) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise DatasetError(f"unsafe dataset path: {relative}")
        joined = root / candidate
        probe = root
        for part in candidate.parts:
            probe /= part
            if probe.is_symlink():
                raise DatasetError(f"symlink is not allowed: {relative}")
        try:
            resolved = joined.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise DatasetError(f"dataset path is missing or escapes root: {relative}") from exc
        if file and not resolved.is_file():
            raise DatasetError(f"expected regular file: {relative}")
        if not file and not resolved.is_dir():
            raise DatasetError(f"expected directory: {relative}")
        return resolved

    def _hash_tree(self, root: Path) -> str:
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise DatasetError(f"symlink is not allowed in fixture: {path}")
            if path.is_file():
                entries.append(
                    {"path": path.relative_to(root).as_posix(), "sha256": self._hash_file(path)}
                )
        if not entries:
            raise DatasetError(f"fixture must contain at least one file: {root}")
        return stable_sha256(entries)

    def _published_fixture_hash(self, root: Path) -> str:
        """Recompute the generator's immutable fixture digest.

        Dataset ingestion predates published Benchmark manifests and deliberately
        uses a different, mapping-based tree payload in ``_hash_tree``.  Keep that
        identity stable while matching the generator's tuple-based publication
        digest for tamper checks.
        """
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise DatasetError(f"symlink is not allowed in fixture: {path}")
            if path.is_file():
                entries.append((path.relative_to(root).as_posix(), self._hash_file(path)))
        if not entries:
            raise DatasetError(f"fixture must contain at least one file: {root}")
        return stable_sha256(entries)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
