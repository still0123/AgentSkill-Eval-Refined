"""Content-addressed artifact manifest contracts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Tuple

from pydantic import Field, field_validator, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest
from agentskill_eval_contracts.enums import ArtifactSensitivity
from agentskill_eval_contracts.experiment import SCHEMA_VERSION, SchemaVersion


class ArtifactEntry(FrozenModel):
    path: str = Field(min_length=1)
    sha256: HexDigest
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    sensitivity: ArtifactSensitivity = ArtifactSensitivity.INTERNAL

    @field_validator("path")
    @classmethod
    def path_must_be_portable_and_relative(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("artifact paths must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {".", ""}:
            raise ValueError("artifact path must be relative and cannot contain '..'")
        if str(path) != value:
            raise ValueError("artifact path must be in canonical POSIX form")
        return value


class ArtifactManifest(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    artifacts: Tuple[ArtifactEntry, ...] = ()

    @model_validator(mode="after")
    def artifact_paths_must_be_unique(self) -> "ArtifactManifest":
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        return self
