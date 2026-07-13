"""Small, evidence-citing Trace Intelligence vertical slice."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Sequence, Tuple, cast
from uuid import UUID

from pydantic import JsonValue

from agentskill_eval_contracts import (
    AttributionRole,
    DiagnosticFinding,
    EvaluationOutcome,
    EventCountDelta,
    FailureDiagnosis,
    FailureLabel,
    PairTraceDiff,
    TraceAvailability,
    TraceCapability,
    TraceEvent,
    TraceManifest,
)

_Clock = Callable[[], datetime]
_TraceSource = Literal["platform", "runner", "agent", "mcp", "rag", "memory", "judge"]
_TraceStatus = Literal["started", "completed", "failed", "cancelled"]


class TraceCollector:
    """Normalize bounded event summaries without retaining configured Secret values."""

    def __init__(
        self,
        run_id: UUID,
        attempt_id: UUID,
        *,
        secrets: Optional[Mapping[str, str]] = None,
        clock: Optional[_Clock] = None,
        max_events: int = 10_000,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self.run_id = run_id
        self.attempt_id = attempt_id
        self._secret_values = tuple(value for value in (secrets or {}).values() if value)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_events = max_events
        self._dropped_events = 0
        self._events: list[TraceEvent] = []

    def record(
        self,
        kind: str,
        *,
        source: _TraceSource = "platform",
        status: Optional[_TraceStatus] = None,
        summary: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if len(self._events) >= self._max_events:
            self._dropped_events += 1
            return
        self._events.append(
            TraceEvent(
                sequence_no=len(self._events) + 1,
                occurred_at=self._clock(),
                kind=kind,
                source=source,
                status=status,
                summary=self._sanitize_mapping(summary or {}),
            )
        )

    async def accept_runner_event(self, kind: str, payload: Mapping[str, Any]) -> None:
        source: _TraceSource = "runner"
        if kind.startswith(("tool.", "file.", "command.", "test.")):
            source = "agent"
        elif kind.startswith("mcp."):
            source = "mcp"
        elif kind.startswith("retrieval."):
            source = "rag"
        elif kind.startswith("memory."):
            source = "memory"
        elif kind.startswith("judge."):
            source = "judge"
        self.record(kind, source=source, summary=payload)

    def manifest(self) -> TraceManifest:
        events = list(self._events)
        if self._dropped_events:
            events.append(
                TraceEvent(
                    sequence_no=len(events) + 1,
                    occurred_at=self._clock(),
                    kind="platform.trace_truncated",
                    source="platform",
                    status="completed",
                    summary={"dropped_events": self._dropped_events},
                )
            )
        kinds = {event.kind for event in events}
        sources = {event.source for event in events}
        return TraceManifest(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            capabilities=(
                self._capability(
                    "post_run_result",
                    "platform",
                    "platform.runner_execution" in kinds,
                    "Runner execution did not produce a post-run result event",
                ),
                self._capability(
                    "runner_lifecycle",
                    "RunnerAdapter event sink",
                    "runner" in sources,
                    "No Runner lifecycle event was observed for this Attempt",
                ),
                self._capability(
                    "tool_file_command",
                    "Agent runtime",
                    "agent" in sources,
                    "Current Attempt exposed no normalized tool/file/command event",
                ),
                self._capability(
                    "mcp_rag_memory",
                    "Platform proxy/lab",
                    bool({"mcp", "rag", "memory"}.intersection(sources)),
                    "No MCP/RAG/Memory proxy event was observed for this Attempt",
                ),
            ),
            events=tuple(events),
        )

    @staticmethod
    def _capability(
        name: str, source: str, observed: bool, unavailable_reason: str
    ) -> TraceCapability:
        return TraceCapability(
            name=name,
            availability=(
                TraceAvailability.OBSERVED
                if observed
                else TraceAvailability.UNAVAILABLE
            ),
            source=source,
            reason=None if observed else unavailable_reason,
        )

    def _sanitize_mapping(self, value: Mapping[str, Any]) -> Dict[str, JsonValue]:
        items = sorted(value.items(), key=lambda item: str(item[0]))[:30]
        return {
            self._redact_text(str(key), limit=100): self._sanitize_json(item, depth=0)
            for key, item in items
        }

    def _sanitize_json(self, value: Any, *, depth: int) -> JsonValue:
        if depth >= 3:
            return "[TRUNCATED]"
        if value is None or isinstance(value, (bool, int)):
            return cast(JsonValue, value)
        if isinstance(value, float):
            return value if isfinite(value) else "[NON_FINITE]"
        if isinstance(value, str):
            return self._redact_text(value, limit=1000)
        if isinstance(value, Mapping):
            return {
                self._redact_text(str(key), limit=100): self._sanitize_json(
                    item, depth=depth + 1
                )
                for key, item in sorted(value.items(), key=lambda item: str(item[0]))[:30]
            }
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return [self._sanitize_json(item, depth=depth + 1) for item in value[:20]]
        return self._redact_text(str(value), limit=1000)

    def _redact_text(self, value: str, *, limit: int) -> str:
        for secret in self._secret_values:
            value = value.replace(secret, "[REDACTED]")
        return value[:limit]


class RuleFailureDiagnoser:
    """Apply only deterministic rules and abstain when trace evidence is insufficient."""

    def diagnose(
        self,
        trace: TraceManifest,
        outcome: Optional[EvaluationOutcome],
        *,
        error_code: Optional[str] = None,
    ) -> FailureDiagnosis:
        if outcome == EvaluationOutcome.PASS:
            return FailureDiagnosis(
                run_id=trace.run_id,
                attempt_id=trace.attempt_id,
                status="no_failure",
            )
        evidence = (trace.events[-1].sequence_no,) if trace.events else ()
        if outcome == EvaluationOutcome.INVALID:
            label, rule_id, rationale = self._invalid_rule(error_code)
            return FailureDiagnosis(
                run_id=trace.run_id,
                attempt_id=trace.attempt_id,
                status="diagnosed",
                findings=(
                    DiagnosticFinding(
                        label=label,
                        role=AttributionRole.ROOT_CAUSE,
                        confidence=1,
                        rule_id=rule_id,
                        evidence_sequence_nos=evidence,
                        rationale=rationale,
                    ),
                ),
            )
        return FailureDiagnosis(
            run_id=trace.run_id,
            attempt_id=trace.attempt_id,
            status="abstained",
            findings=(
                DiagnosticFinding(
                    label=FailureLabel.UNKNOWN,
                    role=AttributionRole.ROOT_CAUSE,
                    confidence=0,
                    rule_id="rule.insufficient_observable_trace",
                    evidence_sequence_nos=evidence,
                    rationale=(
                        "The task failed, but current Runner evidence does not expose enough "
                        "tool, file, command, or verification events for a supported attribution."
                    ),
                ),
            ),
        )

    @staticmethod
    def _invalid_rule(error_code: Optional[str]) -> Tuple[FailureLabel, str, str]:
        code = error_code or "unknown_infrastructure_failure"
        if "timeout" in code or code in {"budget_exhausted", "turn_limit"}:
            return (
                FailureLabel.BUDGET,
                "rule.timeout_or_budget",
                f"The platform recorded a timeout or budget terminal reason: {code}.",
            )
        if "judge" in code or "grader" in code:
            return (
                FailureLabel.JUDGE,
                "rule.judge_infrastructure",
                f"The evaluation infrastructure recorded a Judge/Grader failure: {code}.",
            )
        return (
            FailureLabel.ENVIRONMENT,
            "rule.infrastructure_terminal",
            f"The run was invalidated by an observable infrastructure condition: {code}.",
        )


def compare_traces(
    pair_block_id: UUID,
    control: TraceManifest,
    treatment: TraceManifest,
) -> PairTraceDiff:
    control_kinds = tuple(event.kind for event in control.events)
    treatment_kinds = tuple(event.kind for event in treatment.events)
    control_counts = Counter(control_kinds)
    treatment_counts = Counter(treatment_kinds)
    kinds = sorted(set(control_counts) | set(treatment_counts))
    return PairTraceDiff(
        pair_block_id=pair_block_id,
        control_run_id=control.run_id,
        treatment_run_id=treatment.run_id,
        control_attempt_id=control.attempt_id,
        treatment_attempt_id=treatment.attempt_id,
        sequence_edit_distance=_edit_distance(control_kinds, treatment_kinds),
        event_count_deltas=tuple(
            EventCountDelta(
                kind=kind,
                control_count=control_counts[kind],
                treatment_count=treatment_counts[kind],
                delta=treatment_counts[kind] - control_counts[kind],
            )
            for kind in kinds
        ),
    )


def _edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]
