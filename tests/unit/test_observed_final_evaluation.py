from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from agentskill_eval_contracts import (
    CandidateEvaluation,
    FinalEvaluationJob,
    FinalEvaluationStage,
    SearchCaseResult,
    SearchEvaluationStage,
)
from agentskill_eval_skill_optimizer.final_evaluation import (
    IndependentFinalEvaluator,
    _PreparedDataset,
)
from agentskill_eval_skill_optimizer.final_spec import FinalGateSpec
from agentskill_eval_skill_optimizer.real_evaluator import RealAgentCandidateEvaluator
from agentskill_eval_skill_optimizer.spec import SearchCase


class _ObservedPairEvaluator:
    evaluator_sha256 = "e" * 64

    def __init__(self) -> None:
        self.baseline_results = {
            "case-one": SearchCaseResult(
                case_id="case-one",
                passed=False,
                score=0,
                input_tokens=10,
                output_tokens=2,
                latency_ms=5,
                cost_microusd=3,
                outcome="fail",
                experiment_id=uuid4(),
                run_id=uuid4(),
                attempt_id=uuid4(),
            )
        }

    def evaluate(
        self,
        skill_file: Path,
        dataset_file: Path,
        dataset_sha256: str,
        cases: tuple[SearchCase, ...],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
    ) -> CandidateEvaluation:
        del skill_file, dataset_file, timeout_seconds
        result = SearchCaseResult(
            case_id=cases[0].id,
            passed=True,
            score=1,
            input_tokens=12,
            output_tokens=3,
            latency_ms=7,
            cost_microusd=4,
            outcome="pass",
            experiment_id=uuid4(),
            run_id=uuid4(),
            attempt_id=uuid4(),
        )
        return CandidateEvaluation(
            stage=stage,
            dataset_sha256=dataset_sha256,
            evaluator_sha256=self.evaluator_sha256,
            case_ids=(result.case_id,),
            results=(result,),
            pass_rate=1,
            mean_score=1,
            total_tokens=15,
            total_latency_ms=7,
            total_cost_microusd=4,
            simulated=False,
            evaluated_at=datetime.now(timezone.utc),
        )

    def baseline_evaluation(
        self,
        dataset_sha256: str,
        cases: tuple[SearchCase, ...],
        stage: SearchEvaluationStage,
    ) -> CandidateEvaluation:
        result = self.baseline_results[cases[0].id]
        return CandidateEvaluation(
            stage=stage,
            dataset_sha256=dataset_sha256,
            evaluator_sha256=self.evaluator_sha256,
            case_ids=(result.case_id,),
            results=(result,),
            pass_rate=0,
            mean_score=0,
            total_tokens=12,
            total_latency_ms=5,
            total_cost_microusd=3,
            simulated=False,
            evaluated_at=datetime.now(timezone.utc),
        )


def test_real_final_pair_reconstructs_baseline_and_winner() -> None:
    prepared = _PreparedDataset(
        source=Path("dataset"),
        sha256="d" * 64,
        cases=(SearchCase(id="case-one"),),
        groups={"case-one": "independent-one"},
        simulated=False,
        curated=True,
    )
    evaluator = cast(RealAgentCandidateEvaluator, _ObservedPairEvaluator())
    job = cast(
        FinalEvaluationJob,
        SimpleNamespace(stage=FinalEvaluationStage.VALIDATION_CONFIRM),
    )

    baseline, winner = IndependentFinalEvaluator._real_paired_runs(
        evaluator,
        job,
        prepared,
        Path("dataset.yaml"),
        Path("winner-SKILL.md"),
        30,
    )

    assert baseline[0].pass_rate == 0
    assert winner[0].pass_rate == 1
    assert baseline[0].simulated is False
    assert winner[0].stage == SearchEvaluationStage.VALIDATION_CONFIRM


def test_final_gate_can_explicitly_confirm_no_loss() -> None:
    assert FinalGateSpec(min_absolute_gain=0).min_absolute_gain == 0
