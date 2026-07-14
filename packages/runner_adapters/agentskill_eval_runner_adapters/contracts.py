"""Public contracts for runner anti-corruption adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Protocol, Tuple


class RunnerStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"


class ExitReason(str, Enum):
    COMPLETED = "completed"
    CASE_FAILED = "case_failed"
    EXECUTION_ERROR = "execution_error"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TURN_LIMIT = "turn_limit"
    LOOP_DETECTED = "loop_detected"
    CLI_ERROR = "cli_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    MISSING_REPORT = "missing_report"


class CapabilityLevel(str, Enum):
    NATIVE = "native"
    EMULATED = "emulated"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RunnerCompatibility:
    name: str
    version: str
    binary_sha256: str
    capabilities: Mapping[str, CapabilityLevel]


@dataclass(frozen=True)
class RunnerRequest:
    execution_id: str
    case_id: str
    variant: str
    source_eval_dir: Path
    case_file: Path
    run_dir: Path
    engine: Mapping[str, Any]
    environment: Mapping[str, Any]
    timeout_seconds: int
    max_turns: int
    skill_path: Optional[Path] = None
    mcp: Mapping[str, Any] = field(default_factory=lambda: {"servers": []})
    collect_artifacts: Tuple[str, ...] = ()
    agent_home_files: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    secret_env: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    stdout: str = ""
    stderr: str = ""
    errors: Tuple[str, ...] = ()
    skill_evidence: Optional["RunnerSkillEvidence"] = None


@dataclass(frozen=True)
class RunnerSkillEvidence:
    skill_expected: bool
    installed: Optional[bool]
    baseline_clean: Optional[bool]
    installation_method: str
    compiled_eval_sha256: Optional[str] = None
    installed_skill_sha256: Optional[str] = None
    discovered: Optional[bool] = None
    read: Optional[bool] = None
    activated: Optional[bool] = None
    followed: Optional[bool] = None
    unavailable_reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactObservation:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class RunnerEvent:
    execution_id: str
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)


TraceEventSink = Callable[[RunnerEvent], Awaitable[None]]


async def null_event_sink(event: RunnerEvent) -> None:
    """Discard an event while preserving the async sink contract."""


@dataclass(frozen=True)
class RunnerResult:
    execution_id: str
    case_id: str
    status: RunnerStatus
    exit_reason: ExitReason
    process_exit_code: Optional[int]
    duration_ms: Optional[int] = None
    turns: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    tool_calls: Optional[int] = None
    cost_microusd: Optional[int] = None
    final_message: str = ""
    grading: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Tuple[ArtifactObservation, ...] = ()
    stdout: str = ""
    stderr: str = ""
    raw_result: Mapping[str, Any] = field(default_factory=dict)


class RunnerAdapter(Protocol):
    @property
    def compatibility(self) -> RunnerCompatibility: ...

    async def validate(self, request: RunnerRequest) -> ValidationReport: ...

    async def execute(
        self, request: RunnerRequest, event_sink: TraceEventSink = null_event_sink
    ) -> RunnerResult: ...

    async def cancel(self, execution_id: str) -> bool: ...


JsonDict = Dict[str, Any]
