"""Deterministic local and hardened process adapters for retrieval and Memory."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from agentskill_eval_memory_rag_lab.contracts import Document, FailureKind


@dataclass(frozen=True)
class AdapterCapabilities:
    identity: str
    simulated: bool
    supports_failure_injection: bool
    max_response_bytes: int


@dataclass(frozen=True)
class FailureInjection:
    target: str
    kind: FailureKind
    fail_attempts: int = 1
    latency_ms: float = 0.0


@dataclass(frozen=True)
class RetrievedDocument:
    document_id: str
    score: float
    rank: int
    relevance_grade: int
    stale: bool
    conflicting: bool
    poisoned: bool
    sensitive: bool
    retrieval_cost_usd: float


@dataclass(frozen=True)
class RetrievalResult:
    ok: bool
    documents: Tuple[RetrievedDocument, ...] = ()
    filtered_document_ids: Tuple[str, ...] = ()
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    error: Optional[FailureKind] = None
    message: str = ""


class RetrieverAdapter(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def retrieve(self, query: str, k: int, mode: str = "ranked") -> RetrievalResult: ...


def deterministic_embedding(text: str, dimensions: int = 16) -> Tuple[float, ...]:
    vector = [0.0] * dimensions
    for token in re.findall(r"[\w-]+", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:2], "big") % dimensions] += 1.0 if digest[2] % 2 else -1.0
    magnitude = math.sqrt(sum(item * item for item in vector))
    if magnitude == 0:
        return tuple(vector)
    return tuple(item / magnitude for item in vector)


def _cosine(left: Tuple[float, ...], right: Tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


class MockRetrieverAdapter(RetrieverAdapter):
    def __init__(
        self,
        documents: Sequence[Document],
        failures: Sequence[FailureInjection] = (),
        *,
        duplicate_first: bool = False,
    ) -> None:
        self._documents = tuple(documents)
        self._failures = {item.target: item for item in failures}
        self._attempts = 0
        self._duplicate_first = duplicate_first

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities("mock-retriever-v1", True, True, 1_000_000)

    def retrieve(self, query: str, k: int, mode: str = "ranked") -> RetrievalResult:
        self._attempts += 1
        failure = self._failures.get("retrieve")
        if failure and self._attempts <= failure.fail_attempts:
            return RetrievalResult(
                False,
                latency_ms=failure.latency_ms,
                error=failure.kind,
                message=f"injected {failure.kind.value}",
            )
        if mode == "none":
            return RetrievalResult(True)
        query_vector = deterministic_embedding(query)
        candidates = list(self._documents)
        filtered: list[str] = []
        if mode == "clean":
            retained = []
            for item in candidates:
                if item.stale or item.conflicting or item.poisoned or item.sensitive:
                    filtered.append(item.document_id)
                else:
                    retained.append(item)
            candidates = retained
        ranked = sorted(
            candidates,
            key=lambda item: (
                -(_cosine(query_vector, deterministic_embedding(item.text)) + item.ranking_bias),
                item.document_id,
            ),
        )[:k]
        if self._duplicate_first and ranked and len(ranked) < k:
            ranked.append(ranked[0])
        documents = tuple(
            RetrievedDocument(
                item.document_id,
                round(
                    _cosine(query_vector, deterministic_embedding(item.text)) + item.ranking_bias,
                    8,
                ),
                rank,
                item.relevance_grade,
                item.stale,
                item.conflicting,
                item.poisoned,
                item.sensitive,
                item.retrieval_cost_usd,
            )
            for rank, item in enumerate(ranked, start=1)
        )
        return RetrievalResult(
            True,
            documents,
            tuple(filtered),
            latency_ms=1.0 + len(candidates) * 0.1,
            cost_usd=sum(item.retrieval_cost_usd for item in ranked),
        )


@dataclass(frozen=True)
class MemoryResult:
    ok: bool
    status: str
    value: Optional[str] = None
    latency_ms: float = 0.0
    error: Optional[FailureKind] = None
    message: str = ""
    poisoned: bool = False


class MemoryAdapter(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def apply(
        self,
        operation: str,
        session_id: str,
        key: str,
        value: Optional[str] = None,
        ttl_steps: Optional[int] = None,
    ) -> MemoryResult: ...


@dataclass
class _MemoryEntry:
    value: str
    expires_at: Optional[int]


class MockMemoryAdapter(MemoryAdapter):
    POISON_PATTERNS = ("ignore previous", "system prompt", "exfiltrate", "override policy")

    def __init__(
        self,
        *,
        forbidden_keys: Sequence[str] = (),
        sensitive_keys: Sequence[str] = (),
        failures: Sequence[FailureInjection] = (),
    ) -> None:
        self._entries: Dict[Tuple[str, str], _MemoryEntry] = {}
        self._forbidden = set(forbidden_keys)
        self._sensitive = set(sensitive_keys)
        self._failures = {item.target: item for item in failures}
        self._attempts: Dict[str, int] = {}
        self._tick = 0

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities("mock-memory-v1", True, True, 1_000_000)

    def apply(
        self,
        operation: str,
        session_id: str,
        key: str,
        value: Optional[str] = None,
        ttl_steps: Optional[int] = None,
    ) -> MemoryResult:
        self._tick += 1
        attempt = self._attempts.get(operation, 0) + 1
        self._attempts[operation] = attempt
        failure = self._failures.get(operation)
        if failure and attempt <= failure.fail_attempts:
            return MemoryResult(
                False,
                "failed",
                latency_ms=failure.latency_ms,
                error=failure.kind,
                message=f"injected {failure.kind.value}",
            )
        identity = (session_id, key)
        entry = self._entries.get(identity)
        if entry and entry.expires_at is not None and self._tick >= entry.expires_at:
            self._entries.pop(identity, None)
            entry = None
            if operation in {"read", "expire"}:
                return MemoryResult(True, "expired")
        if operation in {"write", "update", "overwrite"}:
            if key in self._forbidden or key in self._sensitive:
                return MemoryResult(False, "rejected", message="memory key is not persistable")
            if value is None:
                return MemoryResult(False, "rejected", message="memory value is required")
            poisoned = contains_poison_pattern(value)
            if poisoned:
                return MemoryResult(
                    False, "rejected", message="memory poisoning detected", poisoned=True
                )
            if operation == "update" and entry is None:
                return MemoryResult(False, "missing", message="cannot update missing memory")
            expires_at = self._tick + ttl_steps if ttl_steps is not None else None
            self._entries[identity] = _MemoryEntry(value, expires_at)
            return MemoryResult(True, "ok", value=value)
        if operation == "read":
            return (
                MemoryResult(True, "ok", value=entry.value)
                if entry
                else MemoryResult(True, "missing")
            )
        if operation in {"delete", "forget"}:
            existed = self._entries.pop(identity, None) is not None
            return MemoryResult(True, "ok" if existed else "missing")
        if operation == "expire":
            if entry is None:
                return MemoryResult(True, "missing")
            self._entries.pop(identity, None)
            return MemoryResult(True, "expired")
        return MemoryResult(False, "rejected", message="unsupported memory operation")


def contains_poison_pattern(value: Optional[str]) -> bool:
    if value is None:
        return False
    return any(pattern in value.lower() for pattern in MockMemoryAdapter.POISON_PATTERNS)


@dataclass(frozen=True)
class ProcessAdapterConfig:
    executable: Path
    version: str
    sha256: str
    argv: Tuple[str, ...] = ()
    timeout_seconds: float = 5.0
    max_response_bytes: int = 1_000_000
    max_json_depth: int = 20
    max_json_fields: int = 1_000
    allowed_environment: Tuple[str, ...] = ("PATH", "LANG", "LC_ALL")


class _ProcessJsonClient:
    def __init__(self, config: ProcessAdapterConfig) -> None:
        if not config.version.strip():
            raise ValueError("process adapter version must be non-empty")
        if config.executable.is_symlink():
            raise ValueError("process adapter executable must not be a symlink")
        executable = config.executable.resolve(strict=True)
        if not executable.is_file():
            raise ValueError("process adapter executable must be a regular file")
        if hashlib.sha256(executable.read_bytes()).hexdigest() != config.sha256:
            raise ValueError("process adapter executable SHA-256 mismatch")
        self.config = config
        self.executable = executable

    def call(
        self, payload: Mapping[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[FailureKind], str]:
        request = json.dumps(dict(payload), separators=(",", ":")).encode() + b"\n"
        env = {key: os.environ[key] for key in self.config.allowed_environment if key in os.environ}
        process = subprocess.Popen(
            [str(self.executable), *self.config.argv],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(request, timeout=self.config.timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return None, FailureKind.TIMEOUT, "process timeout"
        if len(stdout) > self.config.max_response_bytes:
            return None, FailureKind.MALFORMED_RESPONSE, "response too large"
        try:
            response = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, FailureKind.MALFORMED_RESPONSE, "invalid JSON"
        if not isinstance(response, dict) or not _json_within_limits(
            response, self.config.max_json_depth, self.config.max_json_fields
        ):
            return None, FailureKind.MALFORMED_RESPONSE, "invalid response structure"
        if process.returncode != 0 or response.get("ok") is not True:
            return None, FailureKind.PERMANENT, "process adapter error"
        return response, None, ""


class ProcessRetrieverAdapter(RetrieverAdapter):
    def __init__(self, config: ProcessAdapterConfig) -> None:
        self._client = _ProcessJsonClient(config)

    @property
    def capabilities(self) -> AdapterCapabilities:
        config = self._client.config
        return AdapterCapabilities(
            f"process-retriever@{config.version}", False, False, config.max_response_bytes
        )

    def retrieve(self, query: str, k: int, mode: str = "ranked") -> RetrievalResult:
        response, error, message = self._client.call(
            {"operation": "retrieve", "query": query, "k": k, "mode": mode}
        )
        if response is None:
            return RetrievalResult(False, error=error, message=message)
        raw_documents = response.get("documents")
        if not isinstance(raw_documents, list) or not all(
            isinstance(item, dict) for item in raw_documents
        ):
            return RetrievalResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="documents must be a list"
            )
        try:
            documents = tuple(
                RetrievedDocument(
                    str(item["document_id"]),
                    float(item["score"]),
                    rank,
                    int(item.get("relevance_grade", 0)),
                    bool(item.get("stale", False)),
                    bool(item.get("conflicting", False)),
                    bool(item.get("poisoned", False)),
                    bool(item.get("sensitive", False)),
                    float(item.get("retrieval_cost_usd", 0)),
                )
                for rank, item in enumerate(raw_documents, start=1)
            )
        except (KeyError, TypeError, ValueError):
            return RetrievalResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="invalid document result"
            )
        return RetrievalResult(
            True,
            documents,
            latency_ms=float(response.get("latency_ms", 0)),
            cost_usd=float(response.get("cost_usd", 0)),
        )


class ProcessMemoryAdapter(MemoryAdapter):
    def __init__(self, config: ProcessAdapterConfig) -> None:
        self._client = _ProcessJsonClient(config)

    @property
    def capabilities(self) -> AdapterCapabilities:
        config = self._client.config
        return AdapterCapabilities(
            f"process-memory@{config.version}", False, False, config.max_response_bytes
        )

    def apply(
        self,
        operation: str,
        session_id: str,
        key: str,
        value: Optional[str] = None,
        ttl_steps: Optional[int] = None,
    ) -> MemoryResult:
        response, error, message = self._client.call(
            {
                "operation": operation,
                "session_id": session_id,
                "key": key,
                "value": value,
                "ttl_steps": ttl_steps,
            }
        )
        if response is None:
            return MemoryResult(False, "failed", error=error, message=message)
        status = response.get("status")
        if status not in {"ok", "missing", "rejected", "expired"}:
            return MemoryResult(
                False, "failed", error=FailureKind.MALFORMED_RESPONSE, message="invalid status"
            )
        result_value = response.get("value")
        if result_value is not None and not isinstance(result_value, str):
            return MemoryResult(
                False, "failed", error=FailureKind.MALFORMED_RESPONSE, message="invalid value"
            )
        return MemoryResult(True, status, value=result_value)


def _json_within_limits(value: Any, max_depth: int, max_fields: int) -> bool:
    fields = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal fields
        if depth > max_depth:
            return False
        if isinstance(item, dict):
            fields += len(item)
            return fields <= max_fields and all(visit(child, depth + 1) for child in item.values())
        if isinstance(item, list):
            return all(visit(child, depth + 1) for child in item)
        return True

    return visit(value, 1)
