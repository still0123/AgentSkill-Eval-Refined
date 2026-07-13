"""Deterministic runner used by orchestration and failure-path tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Dict, Optional

from agentskill_eval_runner_adapters.contracts import (
    CapabilityLevel,
    ExitReason,
    RunnerCompatibility,
    RunnerEvent,
    RunnerRequest,
    RunnerResult,
    RunnerSkillEvidence,
    RunnerStatus,
    TraceEventSink,
    ValidationReport,
    null_event_sink,
)


class MockRunnerAdapter:
    def __init__(
        self,
        results: Optional[Dict[str, RunnerResult]] = None,
        delay_seconds: float = 0,
    ) -> None:
        self._results = results or {}
        self._delay_seconds = delay_seconds
        self._tasks: Dict[str, asyncio.Task[RunnerResult]] = {}

    @property
    def compatibility(self) -> RunnerCompatibility:
        return RunnerCompatibility(
            name="mock",
            version="1",
            binary_sha256="0" * 64,
            capabilities={
                "single_case": CapabilityLevel.NATIVE,
                "cancellation": CapabilityLevel.NATIVE,
            },
        )

    async def validate(self, request: RunnerRequest) -> ValidationReport:
        errors = []
        if not request.case_file.is_file():
            errors.append("case_file does not exist")
        if request.skill_path is not None and not (request.skill_path / "SKILL.md").is_file():
            errors.append("skill_path does not contain SKILL.md")
        return ValidationReport(
            valid=not errors,
            errors=tuple(errors),
            skill_evidence=RunnerSkillEvidence(
                skill_expected=request.skill_path is not None,
                installed=None,
                baseline_clean=None,
                installation_method="unsupported_by_mock_runner",
                unavailable_reasons={
                    "installed": "MockRunnerAdapter does not install Skills",
                    "baseline_clean": "MockRunnerAdapter does not compile an Agent workspace",
                    "discovered": "unsupported by MockRunnerAdapter",
                    "read": "unsupported by MockRunnerAdapter",
                    "activated": "unsupported by MockRunnerAdapter",
                    "followed": "unsupported by MockRunnerAdapter",
                },
            ),
        )

    async def execute(
        self, request: RunnerRequest, event_sink: TraceEventSink = null_event_sink
    ) -> RunnerResult:
        await event_sink(RunnerEvent(request.execution_id, "runner.started", {"adapter": "mock"}))

        async def operation() -> RunnerResult:
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            configured = self._results.get(f"{request.case_id}:{request.variant}")
            if configured is None:
                configured = self._results.get(request.case_id)
            if configured is not None:
                return replace(configured, execution_id=request.execution_id)
            return RunnerResult(
                execution_id=request.execution_id,
                case_id=request.case_id,
                status=RunnerStatus.PASS,
                exit_reason=ExitReason.COMPLETED,
                process_exit_code=0,
                final_message="mock-pass",
            )

        task = asyncio.create_task(operation())
        self._tasks[request.execution_id] = task
        try:
            result = await task
            await event_sink(
                RunnerEvent(
                    request.execution_id,
                    "runner.finished",
                    {"status": result.status.value},
                )
            )
            return result
        finally:
            self._tasks.pop(request.execution_id, None)

    async def cancel(self, execution_id: str) -> bool:
        task = self._tasks.get(execution_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True
