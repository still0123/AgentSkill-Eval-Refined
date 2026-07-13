"""Immutable execution-input, Skill, and security evidence contracts."""

from __future__ import annotations

from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, computed_field, model_validator

from agentskill_eval_contracts.artifacts import ArtifactEntry
from agentskill_eval_contracts.base import FrozenModel, HexDigest, stable_sha256
from agentskill_eval_contracts.experiment import SCHEMA_VERSION, SchemaVersion


class FrozenInputManifest(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    experiment_id: UUID
    input_kind: Literal["case_source", "skill"]
    owner_id: UUID
    files: Tuple[ArtifactEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def file_paths_must_be_unique_and_sorted(self) -> "FrozenInputManifest":
        paths = [entry.path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("frozen input paths must be unique")
        if paths != sorted(paths):
            raise ValueError("frozen input paths must be sorted")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def tree_sha256(self) -> str:
        return stable_sha256(
            [entry.model_dump(mode="json", round_trip=True) for entry in self.files]
        )


class SkillActivationEvidence(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: UUID
    attempt_id: UUID
    skill_expected: bool
    installed: Optional[bool] = None
    baseline_clean: Optional[bool] = None
    installation_method: str = Field(min_length=1)
    compiled_eval_sha256: Optional[HexDigest] = None
    installed_skill_sha256: Optional[HexDigest] = None
    discovered: Optional[bool] = None
    read: Optional[bool] = None
    activated: Optional[bool] = None
    followed: Optional[bool] = None
    unavailable_reasons: Dict[str, str] = Field(default_factory=dict)


class SecurityScanEvidence(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: UUID
    attempt_id: UUID
    scanner: Literal["exact-secret/v1"] = "exact-secret/v1"
    status: Literal["clean", "blocked", "not_run"]
    configured_secret_count: int = Field(ge=0)
    scanned_files: int = Field(ge=0)
    scanned_bytes: int = Field(ge=0)
    matched_secret_names: Tuple[str, ...] = ()
    note: Optional[str] = None

    @model_validator(mode="after")
    def status_must_match_detected_names(self) -> "SecurityScanEvidence":
        if self.status == "blocked" and not self.matched_secret_names:
            raise ValueError("blocked scans require at least one matched Secret name")
        if self.status != "blocked" and self.matched_secret_names:
            raise ValueError("only blocked scans may contain matched Secret names")
        return self


class ReplayBundleManifest(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    experiment_id: UUID
    scope: Literal["audit_and_reanalysis"] = "audit_and_reanalysis"
    files: Tuple[ArtifactEntry, ...] = Field(min_length=1)
    omitted_runtime_state: Tuple[str, ...] = (
        "index.sqlite",
        "run.lock",
        "external provider requests",
    )

    @model_validator(mode="after")
    def file_paths_must_be_unique_and_sorted(self) -> "ReplayBundleManifest":
        paths = [entry.path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("replay bundle paths must be unique")
        if paths != sorted(paths):
            raise ValueError("replay bundle paths must be sorted")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def bundle_sha256(self) -> str:
        return stable_sha256(
            [entry.model_dump(mode="json", round_trip=True) for entry in self.files]
        )
