from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentskill_eval_contracts import (
    CandidateEvaluation,
    FinalDecision,
    FinalEvaluationJob,
    FinalEvaluationReport,
    FinalEvaluationStage,
    FinalEvaluationStatus,
    LockedTestReceipt,
    PairClassification,
    SearchCaseResult,
    SearchEvaluationStage,
)
from agentskill_eval_experiment.storage.manifests import load_model
from agentskill_eval_skill_optimizer.final_evaluation import (
    FinalEvaluationError,
    FinalEvaluationStore,
    IndependentFinalEvaluator,
    _PreparedDataset,
)
from agentskill_eval_skill_optimizer.final_spec import (
    FinalGateSpec,
    IndependentFinalEvaluationSpec,
)
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


def test_final_gate_can_set_zero_lower_bound() -> None:
    assert FinalGateSpec(min_absolute_gain=0).min_absolute_gain == 0


def test_locked_test_receipt_is_durable_and_one_shot(tmp_path: Path) -> None:
    store = FinalEvaluationStore(tmp_path)
    optimization_job_id = uuid4()
    receipt = LockedTestReceipt(
        optimization_job_id=optimization_job_id,
        final_evaluation_job_id=uuid4(),
        dataset_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        consumed_at=datetime.now(timezone.utc),
    )

    store.reserve_locked_test(receipt)
    path = (
        store.root / "locked-test-receipts" / f"{optimization_job_id}.json"
    )
    assert load_model(path.read_bytes(), LockedTestReceipt) == receipt
    store.reserve_locked_test(receipt)

    other = receipt.model_copy(update={"final_evaluation_job_id": uuid4()})
    with pytest.raises(FinalEvaluationError, match="already consumed"):
        store.reserve_locked_test(other)


def test_invalid_final_pair_is_not_reported_as_a_negative_tie(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    result = SearchCaseResult(
        case_id="case-one",
        passed=False,
        score=0,
        input_tokens=10,
        output_tokens=2,
        latency_ms=5,
        cost_microusd=3,
        outcome="invalid",
    )
    evaluation = CandidateEvaluation(
        stage=SearchEvaluationStage.VALIDATION_CONFIRM,
        dataset_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        case_ids=("case-one",),
        results=(result,),
        pass_rate=0,
        mean_score=0,
        total_tokens=12,
        total_latency_ms=5,
        total_cost_microusd=3,
        simulated=False,
        evaluated_at=now,
    )
    comparisons = IndependentFinalEvaluator._comparisons(
        (evaluation,),
        (evaluation,),
        {"case-one": "independent-one"},
    )
    job = FinalEvaluationJob(
        id=uuid4(),
        optimization_job_id=uuid4(),
        optimization_job_sha256="a" * 64,
        status=FinalEvaluationStatus.RUNNING,
        stage=FinalEvaluationStage.VALIDATION_CONFIRM,
        spec_sha256="c" * 64,
        dataset_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        base_skill_sha256="b" * 64,
        winner_skill_sha256="f" * 64,
        winner_candidate_id=uuid4(),
        repeats=1,
        simulated=False,
        created_at=now,
    )
    spec = cast(
        IndependentFinalEvaluationSpec,
        SimpleNamespace(gates=FinalGateSpec(min_absolute_gain=0)),
    )

    report = IndependentFinalEvaluator(tmp_path)._report(
        job,
        spec,
        (evaluation,),
        (evaluation,),
        comparisons,
    )

    assert comparisons[0].classification == PairClassification.INVALID
    assert report.invalid_count == 1
    assert report.tie_negative_count == 0
    assert report.decision == FinalDecision.INVALID

    failed = result.model_copy(update={"outcome": "fail"})
    valid_evaluation = evaluation.model_copy(update={"results": (failed,)})
    valid_comparisons = IndependentFinalEvaluator._comparisons(
        (valid_evaluation,),
        (valid_evaluation,),
        {"case-one": "independent-one"},
    )
    zero_gain_spec = cast(
        IndependentFinalEvaluationSpec,
        SimpleNamespace(
            gates=FinalGateSpec(min_absolute_gain=0, min_independent_groups=1)
        ),
    )
    zero_gain_report = IndependentFinalEvaluator(tmp_path)._report(
        job,
        zero_gain_spec,
        (valid_evaluation,),
        (valid_evaluation,),
        valid_comparisons,
    )
    assert zero_gain_report.decision == FinalDecision.NOT_CONFIRMED

    payload = report.model_dump(mode="json")
    payload["cases"][0]["classification"] = PairClassification.TIE_NEGATIVE.value
    with pytest.raises(ValidationError, match="classification does not match"):
        FinalEvaluationReport.model_validate(payload)

    payload = report.model_dump(mode="json")
    payload["gain_ci_low"] = 0.5
    with pytest.raises(ValidationError, match="gain interval does not match"):
        FinalEvaluationReport.model_validate(payload)
