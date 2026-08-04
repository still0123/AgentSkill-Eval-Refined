from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from agentskill_eval_contracts import (
    EvaluationOutcome,
    FailureLabel,
    TraceEvent,
    TraceManifest,
)
from agentskill_eval_trace_intelligence import (
    RuleFailureDiagnoser,
    TraceCollector,
    compare_traces,
)


def test_collector_normalizes_bounds_and_redacts_secret_values() -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    collector = TraceCollector(
        uuid4(), uuid4(), secrets={"TOKEN": "do-not-store"}, clock=lambda: now
    )

    collector.record(
        "tool.completed",
        source="agent",
        status="completed",
        summary={
            "nested": {"authorization": "Bearer do-not-store"},
            "oversized": "x" * 1200,
        },
    )
    trace = collector.manifest()

    assert trace.events[0].occurred_at == now
    assert trace.events[0].summary["nested"] == {
        "authorization": "Bearer [REDACTED]"
    }
    assert len(str(trace.events[0].summary["oversized"])) == 1000
    assert "do-not-store" not in trace.model_dump_json()
    capabilities = {item.name: item for item in trace.capabilities}
    assert capabilities["tool_file_command"].availability.value == "observed"
    assert capabilities["tool_file_command"].reason is None


def test_collector_bounds_event_count_and_redacts_non_string_payloads() -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    collector = TraceCollector(
        uuid4(),
        uuid4(),
        secrets={"TOKEN": "do-not-store"},
        clock=lambda: now,
        max_events=1,
    )
    collector.record(
        "runner.started",
        source="runner",
        summary={"do-not-store": b"do-not-store"},
    )
    collector.record("runner.finished", source="runner")

    trace = collector.manifest()

    assert [event.kind for event in trace.events] == [
        "runner.started",
        "platform.trace_truncated",
    ]
    assert trace.events[-1].summary == {"dropped_events": 1}
    assert "do-not-store" not in trace.model_dump_json()


def test_rule_diagnoser_is_deterministic_and_abstains_without_evidence() -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    trace = TraceManifest(
        run_id=uuid4(),
        attempt_id=uuid4(),
        capabilities=(),
        events=(
            TraceEvent(
                sequence_no=1,
                occurred_at=now,
                kind="platform.run_terminal",
                source="platform",
                status="failed",
            ),
        ),
    )
    diagnoser = RuleFailureDiagnoser()

    timeout = diagnoser.diagnose(
        trace, EvaluationOutcome.INVALID, error_code="runner_timeout"
    )
    tool_budget = diagnoser.diagnose(
        trace, EvaluationOutcome.INVALID, error_code="budget_exhausted"
    )
    loop = diagnoser.diagnose(
        trace, EvaluationOutcome.INVALID, error_code="loop_detected"
    )
    unknown = diagnoser.diagnose(trace, EvaluationOutcome.FAIL)
    success = diagnoser.diagnose(trace, EvaluationOutcome.PASS)

    assert timeout.status == "diagnosed"
    assert timeout.findings[0].label == FailureLabel.BUDGET
    assert timeout.findings[0].evidence_sequence_nos == (1,)
    assert tool_budget.findings[0].label == FailureLabel.BUDGET
    assert loop.findings[0].label == FailureLabel.PLANNING
    assert unknown.status == "abstained"
    assert unknown.findings[0].label == FailureLabel.UNKNOWN
    assert success.status == "no_failure"


def test_rule_diagnoser_classifies_observed_verification_failure() -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    trace = TraceManifest(
        run_id=uuid4(),
        attempt_id=uuid4(),
        capabilities=(),
        events=(
            TraceEvent(
                sequence_no=1,
                occurred_at=now,
                kind="verification.test",
                source="runner",
                status="failed",
                summary={"status": "fail"},
            ),
        ),
    )

    diagnosis = RuleFailureDiagnoser().diagnose(trace, EvaluationOutcome.FAIL)

    assert diagnosis.status == "diagnosed"
    assert diagnosis.findings[0].label == FailureLabel.VERIFICATION
    assert diagnosis.findings[0].evidence_sequence_nos == (1,)


def test_pair_trace_diff_uses_event_counts_and_sequence_edit_distance() -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def trace(run_id: UUID, kinds: tuple[str, ...]) -> TraceManifest:
        return TraceManifest(
            run_id=run_id,
            attempt_id=uuid4(),
            capabilities=(),
            events=tuple(
                TraceEvent(
                    sequence_no=index,
                    occurred_at=now,
                    kind=kind,
                    source="platform",
                )
                for index, kind in enumerate(kinds, start=1)
            ),
        )

    control = trace(uuid4(), ("runner.started", "runner.finished"))
    treatment = trace(
        uuid4(), ("runner.started", "tool.completed", "runner.finished")
    )
    diff = compare_traces(uuid4(), control, treatment)

    assert diff.sequence_edit_distance == 1
    by_kind = {item.kind: item for item in diff.event_count_deltas}
    assert by_kind["tool.completed"].delta == 1
    assert by_kind["runner.started"].delta == 0
