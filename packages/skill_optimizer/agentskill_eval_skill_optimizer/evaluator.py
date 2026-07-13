"""Pluggable validation evaluator boundary for Skill search."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from agentskill_eval_contracts import (
    CandidateEvaluation,
    SearchCaseResult,
    SearchEvaluationStage,
    stable_sha256,
)
from agentskill_eval_skill_optimizer.spec import (
    EvaluatorSpec,
    SearchCase,
    ValidationSearchDataset,
)


class EvaluationError(RuntimeError):
    """Raised when an evaluator violates its frozen contract."""


class CandidateEvaluator(Protocol):
    @property
    def evaluator_sha256(self) -> str: ...

    @property
    def simulated(self) -> bool: ...

    def evaluate(
        self,
        skill_file: Path,
        dataset_file: Path,
        dataset_sha256: str,
        cases: Sequence[SearchCase],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
    ) -> CandidateEvaluation: ...


def _aggregate(
    results: Tuple[SearchCaseResult, ...],
    dataset_sha256: str,
    evaluator_sha256: str,
    stage: SearchEvaluationStage,
    simulated: bool,
) -> CandidateEvaluation:
    costs = [item.cost_microusd for item in results]
    total_cost = None if any(item is None for item in costs) else sum(
        item for item in costs if item is not None
    )
    return CandidateEvaluation(
        stage=stage,
        dataset_sha256=dataset_sha256,
        evaluator_sha256=evaluator_sha256,
        case_ids=tuple(item.case_id for item in results),
        results=results,
        pass_rate=sum(item.passed for item in results) / len(results),
        mean_score=sum(item.score for item in results) / len(results),
        total_tokens=sum(item.input_tokens + item.output_tokens for item in results),
        total_latency_ms=sum(item.latency_ms for item in results),
        total_cost_microusd=total_cost,
        simulated=simulated,
        evaluated_at=datetime.now(timezone.utc),
    )


class SimulatedKeywordEvaluator:
    """Explicitly simulated evaluator used only to test the search controller offline."""

    def __init__(self, dataset: ValidationSearchDataset, version: str) -> None:
        self.dataset = dataset
        self._sha = stable_sha256(
            {"type": "simulated_keyword", "version": version, "dataset": dataset.model_dump()}
        )

    @property
    def evaluator_sha256(self) -> str:
        return self._sha

    @property
    def simulated(self) -> bool:
        return True

    def evaluate(
        self,
        skill_file: Path,
        dataset_file: Path,
        dataset_sha256: str,
        cases: Sequence[SearchCase],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
    ) -> CandidateEvaluation:
        del dataset_file, timeout_seconds
        content = skill_file.read_text(encoding="utf-8").lower()
        content_bytes = len(content.encode("utf-8"))
        results = []
        for case in cases:
            matched = sum(term.lower() in content for term in case.required_terms)
            score = matched / len(case.required_terms)
            results.append(
                SearchCaseResult(
                    case_id=case.id,
                    passed=score == 1.0,
                    score=score,
                    input_tokens=max(1, content_bytes // 4),
                    output_tokens=24,
                    latency_ms=10 + content_bytes // 100,
                    cost_microusd=0,
                )
            )
        return _aggregate(
            tuple(results), dataset_sha256, self.evaluator_sha256, stage, simulated=True
        )


class ProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    results: Tuple[SearchCaseResult, ...] = Field(min_length=1)


class ProcessEvaluator:
    """External process bridge for a real Agent/Runner evaluation implementation."""

    def __init__(self, spec: EvaluatorSpec) -> None:
        self.command = spec.command
        self._simulated = spec.simulated
        command_file = Path(self.command[0])
        command_hash = None
        if command_file.is_file():
            command_hash = hashlib.sha256(command_file.read_bytes()).hexdigest()
        self._sha = stable_sha256(
            {
                "type": spec.type,
                "version": spec.version,
                "simulated": spec.simulated,
                "command": list(spec.command),
                "command_file_sha256": command_hash,
            }
        )

    @property
    def evaluator_sha256(self) -> str:
        return self._sha

    @property
    def simulated(self) -> bool:
        return self._simulated

    def evaluate(
        self,
        skill_file: Path,
        dataset_file: Path,
        dataset_sha256: str,
        cases: Sequence[SearchCase],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
    ) -> CandidateEvaluation:
        request = {
            "schema_version": "ase/process-evaluator-request/v1alpha1",
            "skill_file": str(skill_file.resolve(strict=True)),
            "dataset_file": str(dataset_file.resolve(strict=True)),
            "dataset_sha256": dataset_sha256,
            "case_ids": [item.id for item in cases],
            "stage": stage.value,
        }
        try:
            with tempfile.TemporaryDirectory(prefix="ase-process-evaluator-") as runtime_home:
                environment = {
                    "HOME": runtime_home,
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONHASHSEED": "0",
                    "TZ": "UTC",
                }
                completed = subprocess.run(
                    self.command,
                    input=json.dumps(request, sort_keys=True).encode(),
                    capture_output=True,
                    check=False,
                    timeout=timeout_seconds,
                    env=environment,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EvaluationError(f"process evaluator failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")[:2000]
            raise EvaluationError(f"process evaluator exited {completed.returncode}: {detail}")
        try:
            payload = ProcessResult.model_validate_json(completed.stdout)
        except ValueError as exc:
            raise EvaluationError(f"invalid process evaluator JSON: {exc}") from exc
        expected = tuple(item.id for item in cases)
        if tuple(item.case_id for item in payload.results) != expected:
            raise EvaluationError("process evaluator returned wrong case set or order")
        return _aggregate(
            payload.results,
            dataset_sha256,
            self.evaluator_sha256,
            stage,
            simulated=self.simulated,
        )


def build_evaluator(
    spec: EvaluatorSpec, dataset: ValidationSearchDataset
) -> CandidateEvaluator:
    if spec.type == "simulated_keyword":
        return SimulatedKeywordEvaluator(dataset, spec.version)
    return ProcessEvaluator(spec)
