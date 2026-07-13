"""Shared primitives for immutable AgentSkill-Eval contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Dict

from pydantic import BaseModel, ConfigDict, Field, JsonValue

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
JsonObject = Dict[str, JsonValue]


class FrozenModel(BaseModel):
    """Strict immutable base model used by persisted domain contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


def canonical_json(value: object) -> bytes:
    """Encode JSON deterministically for content addressing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def stable_sha256(value: object) -> str:
    """Return the SHA-256 digest of a canonical JSON value."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_text(value: str) -> str:
    """Return the SHA-256 digest of an exact UTF-8 string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
