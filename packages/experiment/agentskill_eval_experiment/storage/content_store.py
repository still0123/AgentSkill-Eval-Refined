"""Unified content-addressable store for immutable manifests and blobs.

Consolidates AtomicFileWriter, ManifestEnvelope, and ContentAddressedBlobStore
into a single namespace-based API.  Each specialized store (BenchmarkStore,
OptimizationStore, …) becomes a thin wrapper or is replaced by a direct
ContentStore namespace.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Final, Literal, Optional, Tuple, Type, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, JsonValue, ValidationError, model_validator

from agentskill_eval_contracts import FrozenModel, HexDigest, stable_sha256
from agentskill_eval_experiment.storage.errors import IntegrityError

STORAGE_SCHEMA_VERSION: Final = "ase/storage/v1"
_TEMP_PATTERN = __import__("re").compile(
    r"^\.tmp-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(.+)$"
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class BlobReference(FrozenModel):
    sha256: HexDigest
    size_bytes: int = 0
    relative_path: str = ""


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


def _semantic_sha256(model: BaseModel) -> Optional[str]:
    for attr in ("variant_sha256", "block_sha256", "idempotency_key", "plan_sha256"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and len(value) == 64:
            return value
    return None


def _envelope_for_model(model: BaseModel) -> ManifestEnvelope:
    payload = model.model_dump(mode="json", round_trip=True)
    return ManifestEnvelope(
        model_name=model.__class__.__name__,
        payload_sha256=stable_sha256(payload),
        semantic_sha256=_semantic_sha256(model),
        payload=payload,
    )


def _model_bytes(model: BaseModel) -> bytes:
    envelope = _envelope_for_model(model)
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


def _load_model(content: bytes, model_type: Type[ModelT]) -> ModelT:
    try:
        decoded = json.loads(content.decode("utf-8"))
        envelope = ManifestEnvelope.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise IntegrityError(f"invalid manifest envelope: {error}") from error
    if envelope.model_name != model_type.__name__:
        raise IntegrityError(
            f"manifest model mismatch: expected {model_type.__name__}, got {envelope.model_name}"
        )
    try:
        model = model_type.model_validate(envelope.payload)
    except ValidationError as error:
        raise IntegrityError(f"invalid {model_type.__name__} payload: {error}") from error
    actual = _semantic_sha256(model)
    if envelope.semantic_sha256 != actual:
        raise IntegrityError(
            "semantic digest mismatch: declared {envelope.semantic_sha256}, actual {actual}"
        )
    return model


class ContentStore:
    """Unified namespace-based store for immutable manifests and blobs.

    Usage::

        store = ContentStore(workspace)
        ns = store.namespace("benchmark-jobs")
        ns.save("job.json", my_job)
        job = ns.load("job.json", BenchmarkJob)
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # -- Namespace -----------------------------------------------------------

    def namespace(self, *parts: str) -> "ContentStore":
        """Return a sub-namespace rooted at ``root / part1 / part2 / …``."""
        return ContentStore(self.root / "/".join(parts))

    def sub(self, entity_id: UUID) -> "ContentStore":
        """Convenience: namespace(str(entity_id))."""
        return self.namespace(str(entity_id))

    # -- Typed manifest save/load --------------------------------------------

    def save(self, name: str, model: BaseModel) -> Path:
        """Atomically write *model* as an integrity-wrapped JSON manifest."""
        target = self.root / name
        content = _model_bytes(model)
        self._atomic_write(target, content)
        return target

    def load(self, name: str, model_type: Type[ModelT]) -> ModelT:
        """Load and integrity-check a typed manifest."""
        return _load_model((self.root / name).read_bytes(), model_type)

    def save_with_sha256(self, name: str, model: BaseModel) -> Tuple[Path, Path]:
        """Write manifest + ``.sha256`` sidecar; return (manifest_path, sha256_path)."""
        manifest_path = self.save(name, model)
        sha256_path = self.root / f"{name}.sha256"
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest().encode("utf-8")
        self._atomic_write(sha256_path, digest)
        return manifest_path, sha256_path

    def exists(self, name: str) -> bool:
        return (self.root / name).is_file()

    # -- Blob (content-addressed) storage ------------------------------------

    def put_blob(self, content: bytes) -> BlobReference:
        """Store content by SHA-256 (deduplicated)."""
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / "sha256" / digest[:2] / digest
        if path.exists():
            existing = path.read_bytes()
            if existing != content:
                raise IntegrityError(f"blob collision at {path}")
        else:
            self._atomic_write(path, content)
        return BlobReference(
            sha256=digest, size_bytes=len(content),
            relative_path=f"sha256/{digest[:2]}/{digest}",
        )

    def read_blob(self, ref: BlobReference) -> bytes:
        """Read and integrity-check a blob."""
        path = self.root / ref.relative_path
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != ref.sha256 or len(content) != ref.size_bytes:
            raise IntegrityError(f"blob integrity check failed for {ref.relative_path}")
        return content

    # -- Raw bytes (non-typed) storage ---------------------------------------

    def write_bytes(self, name: str, content: bytes) -> Path:
        target = self.root / name
        self._atomic_write(target, content)
        return target

    def read_bytes(self, name: str) -> bytes:
        return (self.root / name).read_bytes()

    # -- Directory listing ---------------------------------------------------

    def list_dirs(self) -> Tuple[Path, ...]:
        """Return immediate child directories under the current root."""
        if not self.root.is_dir():
            return ()
        return tuple(sorted(p for p in self.root.iterdir() if p.is_dir()))

    # -- Internals -----------------------------------------------------------

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp-{uuid4()}.{target.name}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, target)
        _fsync_directory(target.parent)