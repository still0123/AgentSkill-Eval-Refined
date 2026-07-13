from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentskill_eval_contracts import (
    FinalDecision,
    FinalEvaluationStage,
    FinalEvaluationStatus,
)
from agentskill_eval_skill_optimizer import (
    BenchmarkGuidedSkillSearch,
    EvaluatorSpec,
    FinalEvaluationError,
    IndependentFinalEvaluationSpec,
    IndependentFinalEvaluator,
    OptimizationSearchSpec,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/optimizer/python-review-search"


def _search(workspace: Path) -> None:
    BenchmarkGuidedSkillSearch(workspace).run(
        OptimizationSearchSpec.load(EXAMPLE / "search.example.yaml")
    )


def test_independent_confirmation_is_paired_frozen_and_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _search(workspace)
    spec = IndependentFinalEvaluationSpec.load(EXAMPLE / "final.example.yaml")
    evaluator = IndependentFinalEvaluator(workspace)

    result = evaluator.run(spec)
    replayed = evaluator.run(spec)

    assert result.job.status == FinalEvaluationStatus.COMPLETED
    assert result.job.stage == FinalEvaluationStage.VALIDATION_CONFIRM
    assert result.report.decision == FinalDecision.CONFIRMED
    assert result.report.base_pass_rate == 0.0
    assert result.report.winner_pass_rate == 1.0
    assert result.report.absolute_gain == 1.0
    assert result.report.gain_ci_low == 1.0
    assert result.report.gain_ci_high == 1.0
    assert result.report.loss_count == 0
    assert result.report.independent_group_count == 4
    assert len(result.report.base_evaluations) == 3
    assert len(result.report.winner_evaluations) == 3
    assert result.job.simulated is True
    assert "not Agent performance evidence" in result.report.claim_limit
    assert replayed.report == result.report

    report_path = result.report_json
    report_bytes = report_path.read_bytes()
    report_path.write_bytes(report_bytes + b"\n")
    with pytest.raises(FinalEvaluationError, match="report integrity mismatch"):
        evaluator.run(spec)
    report_path.write_bytes(report_bytes)

    frozen_winner = evaluator.store.job_dir(result.job.id) / "inputs" / "winner-SKILL.md"
    frozen_winner.write_text("tampered", encoding="utf-8")
    with pytest.raises(FinalEvaluationError, match="winner Skill integrity mismatch"):
        evaluator.run(spec)


def test_locked_test_receipt_prevents_a_second_configuration(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _search(workspace)
    confirm = IndependentFinalEvaluationSpec.load(EXAMPLE / "final.example.yaml")
    payload = yaml.safe_load((EXAMPLE / "final-validation.yaml").read_text(encoding="utf-8"))
    payload["split"] = "locked_test"
    locked_dataset = tmp_path / "final-validation.yaml"
    locked_dataset.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    locked = confirm.model_copy(
        update={"dataset_path": locked_dataset, "stage": "locked_test"}
    )
    evaluator = IndependentFinalEvaluator(workspace)

    first = evaluator.run(locked)
    assert first.job.stage == FinalEvaluationStage.LOCKED_TEST
    assert first.report.decision == FinalDecision.CONFIRMED

    changed = locked.model_copy(
        update={
            "evaluator": EvaluatorSpec(
                type="simulated_keyword",
                version="different-locked-test-config",
                simulated=True,
            )
        }
    )
    with pytest.raises(FinalEvaluationError, match="already consumed"):
        evaluator.run(changed)


def test_final_process_dataset_rejects_wrong_or_mixed_split(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _search(workspace)
    template = IndependentFinalEvaluationSpec.load(EXAMPLE / "final.example.yaml")
    spec = template.model_copy(
        update={
            "dataset_path": ROOT / "examples/datasets/python-review-demo",
            "evaluator": EvaluatorSpec(
                type="process",
                command=("unused-evaluator",),
                version="test-v1",
                simulated=True,
            ),
        }
    )

    with pytest.raises(FinalEvaluationError, match="validation_confirm cases only"):
        IndependentFinalEvaluator(workspace).run(spec)
