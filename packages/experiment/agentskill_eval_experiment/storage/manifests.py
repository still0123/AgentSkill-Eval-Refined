"""Integrity envelope and typed manifest serialization."""

from __future__ import annotations

import json
from typing import Dict, Final, Literal, Optional, Type, TypeVar

from pydantic import BaseModel, JsonValue, ValidationError, model_validator

from agentskill_eval_contracts import FrozenModel, HexDigest, stable_sha256
from agentskill_eval_experiment.storage.errors import IntegrityError

STORAGE_SCHEMA_VERSION: Final = "ase/storage/v1"
ModelT = TypeVar("ModelT", bound=BaseModel)


class ManifestEnvelope(FrozenModel):
    storage_schema_version: Literal["ase/storage/v1"] = STORAGE_SCHEMA_VERSION
    model_name: str
    payload_sha256: HexDigest
    semantic_sha256: Optional[HexDigest] = None
    payload: Dict[str, JsonValue]

    @model_validator(mode="after")
    def payload_hash_must_match(self) -> "ManifestEnvelope":
        actual = stable_sha256(self.payload)
        if actual != self.payload_sha256:
            raise ValueError(
                f"payload digest mismatch: declared {self.payload_sha256}, actual {actual}"
            )
        return self


def semantic_sha256(model: BaseModel) -> Optional[str]:
    """Return the model's domain fingerprint when it exposes one."""
    for attribute in ("variant_sha256", "block_sha256", "idempotency_key", "plan_sha256"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and len(value) == 64:
            return value
    return None


def envelope_for_model(model: BaseModel) -> ManifestEnvelope:
    # round_trip omits computed fields recursively and is supported across Pydantic v2.
    payload = model.model_dump(mode="json", round_trip=True)
    return ManifestEnvelope(
        model_name=model.__class__.__name__,
        payload_sha256=stable_sha256(payload),
        semantic_sha256=semantic_sha256(model),
        payload=payload,
    )


def envelope_bytes(envelope: ManifestEnvelope) -> bytes:
    return (
        json.dumps(
            envelope.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def model_bytes(model: BaseModel) -> bytes:
    return envelope_bytes(envelope_for_model(model))


def parse_envelope(content: bytes) -> ManifestEnvelope:
    try:
        decoded = json.loads(content.decode("utf-8"))
        return ManifestEnvelope.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise IntegrityError(f"invalid manifest envelope: {error}") from error


def load_model(content: bytes, model_type: Type[ModelT]) -> ModelT:
    envelope = parse_envelope(content)
    if envelope.model_name != model_type.__name__:
        raise IntegrityError(
            f"manifest model mismatch: expected {model_type.__name__}, got {envelope.model_name}"
        )
    try:
        model = model_type.model_validate(envelope.payload)
    except ValidationError as error:
        raise IntegrityError(f"invalid {model_type.__name__} payload: {error}") from error

    actual_semantic = semantic_sha256(model)
    if envelope.semantic_sha256 != actual_semantic:
        raise IntegrityError(
            "semantic digest mismatch: "
            f"declared {envelope.semantic_sha256}, actual {actual_semantic}"
        )
    return model
