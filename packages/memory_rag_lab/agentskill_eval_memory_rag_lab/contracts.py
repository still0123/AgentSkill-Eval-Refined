"""Strict contracts for deterministic Memory and RAG evaluation."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FailureKind(str, Enum):
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RATE_LIMIT = "rate_limit"
    MALFORMED_RESPONSE = "malformed_response"
    UNAVAILABLE = "unavailable"


def secure_input_path(path: Path, allowed_root: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError("symbolic-link inputs are not allowed")
    resolved = expanded.resolve(strict=True)
    root = allowed_root.expanduser().resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise ValueError("input path escapes the allowed root")
    if not resolved.is_file():
        raise ValueError("input must be a regular file")
    return resolved


class Document(StrictModel):
    document_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    text: str = Field(min_length=1)
    relevance_grade: int = Field(default=0, ge=0, le=3)
    stale: bool = False
    conflicting: bool = False
    poisoned: bool = False
    sensitive: bool = False
    ranking_bias: float = 0.0
    retrieval_cost_usd: float = Field(default=0, ge=0)


class MemoryExpectation(StrictModel):
    operation: Literal["write", "read", "update", "overwrite", "delete", "expire", "reject"]
    session_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    expected_status: Literal["ok", "missing", "rejected", "expired"]
    expected_value_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Provenance(StrictModel):
    source: str = Field(min_length=1)
    author: str = Field(min_length=1)
    version: str = Field(min_length=1)


class MemoryRagCase(StrictModel):
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: Literal["retrieval_generation", "memory"]
    task: str = Field(min_length=1)
    query: Optional[str] = None
    documents: Tuple[Document, ...] = ()
    gold_document_ids: Tuple[str, ...] = ()
    answer_key: Optional[str] = None
    gold_claims: Tuple[str, ...] = ()
    k: int = Field(default=3, ge=1, le=100)
    memory_expectations: Tuple[MemoryExpectation, ...] = ()
    forbidden_memory_keys: Tuple[str, ...] = ()
    sensitive_memory_keys: Tuple[str, ...] = ()
    independence_group: str = Field(min_length=1)
    provenance: Provenance
    simulated: Literal[True]

    @model_validator(mode="after")
    def validate_case_references(self) -> "MemoryRagCase":
        ids = [item.document_id for item in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")
        gold = set(self.gold_document_ids)
        if len(gold) != len(self.gold_document_ids):
            raise ValueError("gold document IDs must be unique")
        if not gold <= set(ids):
            raise ValueError(f"gold documents do not exist: {sorted(gold - set(ids))}")
        if self.kind == "retrieval_generation":
            if not self.query or not self.documents or not gold or self.answer_key is None:
                raise ValueError(
                    "retrieval_generation cases require query, documents, gold evidence, and answer"
                )
        elif not self.memory_expectations:
            raise ValueError("memory cases require lifecycle expectations")
        return self


class MemoryRagDataset(StrictModel):
    name: str = Field(min_length=1)
    cases: Tuple[MemoryRagCase, ...] = Field(min_length=1)
    simulated: Literal[True]

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> "MemoryRagDataset":
        ids = [item.case_id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case IDs must be unique")
        return self

    @classmethod
    def load(cls, path: Path, *, allowed_root: Optional[Path] = None) -> "MemoryRagDataset":
        safe = secure_input_path(path, allowed_root or path.parent)
        return cls.model_validate(yaml.safe_load(safe.read_text(encoding="utf-8")))


class MemoryRagEventKind(str, Enum):
    RETRIEVAL_QUERY = "retrieval.query"
    RETRIEVAL_RESULT = "retrieval.result"
    RETRIEVAL_FILTERED = "retrieval.filtered"
    RETRIEVAL_FAILED = "retrieval.failed"
    CONTEXT_ASSEMBLED = "context.assembled"
    CONTEXT_TRUNCATED = "context.truncated"
    MEMORY_WRITE = "memory.write"
    MEMORY_READ = "memory.read"
    MEMORY_UPDATE = "memory.update"
    MEMORY_DELETE = "memory.delete"
    MEMORY_EXPIRED = "memory.expired"
    MEMORY_REJECTED = "memory.rejected"
    MEMORY_POISON_DETECTED = "memory.poison_detected"


class MemoryRagTraceEvent(StrictModel):
    attempt_id: UUID
    sequence: int = Field(ge=1)
    timestamp: datetime
    kind: MemoryRagEventKind
    adapter_identity: str = Field(min_length=1)
    session_id_hash: Optional[str] = None
    document_ids: Tuple[str, ...] = ()
    key_hash: Optional[str] = None
    value_summary: Optional[Dict[str, JsonValue]] = None
    status: str = Field(min_length=1)
    latency_ms: float = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    error_category: Optional[str] = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamps must be timezone-aware")
        return value


class MemoryRagTrace(StrictModel):
    run_id: UUID
    case_id: str
    simulated: bool
    events: Tuple[MemoryRagTraceEvent, ...]

    @model_validator(mode="after")
    def sequences_are_contiguous(self) -> "MemoryRagTrace":
        if [item.sequence for item in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("trace sequences must be contiguous and start at one")
        return self


def secret_summary(value: str) -> Dict[str, JsonValue]:
    """Describe a value without retaining sensitive Memory or document text."""
    return {
        "sha256": hashlib.sha256(value.encode()).hexdigest(),
        "length": len(value),
        "redacted": True,
    }


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
