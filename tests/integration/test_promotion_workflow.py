"""Fake handoff to immutable SkillVersion promotion integration tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import PromotionWorkflowRecord, PromotionWorkflowStatus
from agentskill_eval_skill_optimizer import (
    FailureGuidedEvolutionResult,
    FailureGuidedEvolutionSpec,
    FailureGuidedSkillEvolution,
    IndependentFinalEvaluationSpec,
    OptimizationStore,
    PromotionWorkflow,
    PromotionWorkflowError,
)

ROOT = Path(__file__).resolve().parents[2]
EVOLUTION = ROOT / "examples/optimizer/failure-guided/evolution.example.yaml"
runner = CliRunner()


def _evolution(workspace: Path):
    return FailureGuidedSkillEvolution(workspace).run(FailureGuidedEvolutionSpec.load(EVOLUTION))


def _skill_texts(workspace: Path, result: FailureGuidedEvolutionResult) -> tuple[str, str]:
    search = result.search
    store = OptimizationStore(workspace)
    base_candidate = next(item for item in search.candidates if item.origin.value == "original")
    return (
        store.skill_path(base_candidate).read_text(encoding="utf-8"),
        store.skill_path(search.winner).read_text(encoding="utf-8"),
    )


def _unique_terms(base: str, winner: str) -> tuple[str, str]:
    base_words = set(re.findall(r"[a-z]{7,}", base.lower()))
    winner_words = sorted(set(re.findall(r"[a-z]{7,}", winner.lower())) - base_words)
    assert len(winner_words) >= 2
    return winner_words[0], winner_words[1]


def _final_spec(
    tmp_path: Path,
    optimization_job_id: str,
    *,
    split: str,
    terms: tuple[str, str],
    shared_with_base: bool = False,
) -> Path:
    directory = tmp_path / split
    directory.mkdir()
    dataset = directory / "final-validation.yaml"
    required = ("skill", "review") if shared_with_base else terms
    dataset.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/final-evaluation-dataset/v1alpha1",
                "name": f"fixture-{split}",
                "version": "1",
                "split": split,
                "simulated": True,
                "cases": [
                    {
                        "id": f"{split.replace('_', '-')}-a",
                        "independence_group": f"fixture/{split}/a",
                        "required_terms": [required[0]],
                    },
                    {
                        "id": f"{split.replace('_', '-')}-b",
                        "independence_group": f"fixture/{split}/b",
                        "required_terms": [required[1]],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    spec = directory / "final.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/independent-final-evaluation/v1alpha1",
                "name": f"fixture-{split}",
                "optimization_job_id": optimization_job_id,
                "dataset_path": str(dataset),
                "stage": split,
                "repeats": 1,
                "timeout_seconds": 30,
                "gates": {
                    "min_absolute_gain": 0.1,
                    "max_loss_cases": 0,
                    "max_token_overhead_ratio": 2.0,
                    "min_independent_groups": 2,
                    "bootstrap_resamples": 100,
                    "bootstrap_seed": 2026,
                },
                "evaluator": {
                    "type": "simulated_keyword",
                    "version": f"fixture-{split}-v1",
                    "simulated": True,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return spec


def test_fake_handoff_confirmation_locked_review_and_publication(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    evolution = _evolution(workspace)
    base, winner = _skill_texts(workspace, evolution)
    terms = _unique_terms(base, winner)
    service = PromotionWorkflow(workspace)

    workflow = service.begin(
        evolution.handoff_path,
        skill_name="python-review",
        target_version="2.0.0-fixture",
        actor="fake-evolution",
    )
    assert workflow.status == PromotionWorkflowStatus.AWAITING_CONFIRMATION
    assert tuple(item.role for item in workflow.lineage) == (
        "handoff",
        "evolution_report",
        "regression_gate",
        "hypotheses",
        "search_report",
    )

    confirm_spec = IndependentFinalEvaluationSpec.load(
        _final_spec(
            tmp_path,
            str(workflow.optimization_job_id),
            split="validation_confirm",
            terms=terms,
        )
    )
    confirmed = service.confirm(workflow.id, confirm_spec)
    assert confirmed.workflow.status == PromotionWorkflowStatus.AWAITING_LOCKED_TEST

    locked_spec = IndependentFinalEvaluationSpec.load(
        _final_spec(
            tmp_path,
            str(workflow.optimization_job_id),
            split="locked_test",
            terms=terms,
        )
    )
    locked = service.locked_test(workflow.id, locked_spec)
    assert locked.workflow.status == PromotionWorkflowStatus.AWAITING_HUMAN_REVIEW

    approved = service.approve(
        workflow.id, reviewer="fixture-reviewer", reason="all Fake gates passed"
    )
    replay = service.approve(
        workflow.id, reviewer="fixture-reviewer", reason="all Fake gates passed"
    )
    assert approved.workflow.status == PromotionWorkflowStatus.APPROVED
    assert approved.release_manifest is not None
    assert approved.release_manifest.decision == "APPROVED"
    assert approved.publication is not None
    assert approved.publication.manifest.simulated_evidence is True
    assert approved.publication.manifest.metadata["promotion_workflow_id"] == str(workflow.id)
    assert approved.publication.manifest.metadata["lineage_sha256"] == workflow.lineage_sha256
    assert approved.release_manifest.skill_version_manifest_sha256 == (
        approved.workflow.skill_version_manifest_sha256
    )
    assert replay.release_manifest == approved.release_manifest
    assert replay.publication is not None
    assert replay.publication.manifest == approved.publication.manifest
    receipt = (
        workspace
        / "final-evaluations"
        / "locked-test-receipts"
        / f"{workflow.optimization_job_id}.json"
    )
    assert receipt.is_file()


def test_non_confirmed_fixture_is_rejected_without_skill_version(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    evolution = _evolution(workspace)
    workflow = PromotionWorkflow(workspace).begin(
        evolution.handoff_path,
        skill_name="python-review",
        target_version="2.0.0-rejected",
        actor="fake-evolution",
    )
    spec = IndependentFinalEvaluationSpec.load(
        _final_spec(
            tmp_path,
            str(workflow.optimization_job_id),
            split="validation_confirm",
            terms=("unused-a", "unused-b"),
            shared_with_base=True,
        )
    )

    rejected = PromotionWorkflow(workspace).confirm(workflow.id, spec)

    assert rejected.workflow.status == PromotionWorkflowStatus.REJECTED
    assert rejected.release_manifest is not None
    assert rejected.release_manifest.decision == "REJECTED"
    assert rejected.workflow.skill_version_manifest_sha256 is None
    assert not (
        workspace / "skill-version-promotion" / "versions" / "python-review" / "2.0.0-rejected"
    ).exists()


def test_stage4b_refuses_non_simulated_final_evaluator(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    evolution = _evolution(workspace)
    workflow = PromotionWorkflow(workspace).begin(
        evolution.handoff_path,
        skill_name="python-review",
        target_version="2.0.0-no-real",
        actor="fake-evolution",
    )
    spec = IndependentFinalEvaluationSpec.load(
        _final_spec(
            tmp_path,
            str(workflow.optimization_job_id),
            split="validation_confirm",
            terms=_unique_terms(*_skill_texts(workspace, evolution)),
        )
    )
    unsafe = spec.model_copy(
        update={"evaluator": spec.evaluator.model_copy(update={"simulated": False})}
    )

    with pytest.raises(PromotionWorkflowError, match="cannot execute real"):
        PromotionWorkflow(workspace).confirm(workflow.id, unsafe)


def test_human_review_can_reject_after_locked_test(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    evolution = _evolution(workspace)
    terms = _unique_terms(*_skill_texts(workspace, evolution))
    service = PromotionWorkflow(workspace)
    workflow = service.begin(
        evolution.handoff_path,
        skill_name="python-review",
        target_version="2.0.0-human-rejected",
        actor="fake-evolution",
    )
    service.confirm(
        workflow.id,
        IndependentFinalEvaluationSpec.load(
            _final_spec(
                tmp_path,
                str(workflow.optimization_job_id),
                split="validation_confirm",
                terms=terms,
            )
        ),
    )
    service.locked_test(
        workflow.id,
        IndependentFinalEvaluationSpec.load(
            _final_spec(
                tmp_path,
                str(workflow.optimization_job_id),
                split="locked_test",
                terms=terms,
            )
        ),
    )

    rejected = service.reject(
        workflow.id,
        reviewer="fixture-reviewer",
        reason="fixture policy requires rejection",
    )

    assert rejected.workflow.status == PromotionWorkflowStatus.REJECTED
    assert rejected.workflow.human_review is not None
    assert rejected.workflow.human_review.decision == "REJECTED"
    assert rejected.release_manifest is not None
    assert rejected.release_manifest.skill_version_manifest_sha256 is None


def test_approval_recovers_when_release_precedes_workflow_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    evolution = _evolution(workspace)
    terms = _unique_terms(*_skill_texts(workspace, evolution))
    service = PromotionWorkflow(workspace)
    workflow = service.begin(
        evolution.handoff_path,
        skill_name="python-review",
        target_version="2.0.0-recovery",
        actor="fake-evolution",
    )
    service.confirm(
        workflow.id,
        IndependentFinalEvaluationSpec.load(
            _final_spec(
                tmp_path,
                str(workflow.optimization_job_id),
                split="validation_confirm",
                terms=terms,
            )
        ),
    )
    service.locked_test(
        workflow.id,
        IndependentFinalEvaluationSpec.load(
            _final_spec(
                tmp_path,
                str(workflow.optimization_job_id),
                split="locked_test",
                terms=terms,
            )
        ),
    )
    original_save = service.store.save

    def fail_terminal_save(record: PromotionWorkflowRecord) -> None:
        if record.status == PromotionWorkflowStatus.APPROVED:
            raise OSError("simulated crash after immutable release write")
        original_save(record)

    monkeypatch.setattr(service.store, "save", fail_terminal_save)
    with pytest.raises(OSError, match="simulated crash"):
        service.approve(workflow.id, reviewer="fixture-reviewer", reason="fixture approval")
    assert service.store.release_path(workflow.id).is_file()

    monkeypatch.setattr(service.store, "save", original_save)
    recovered = service.approve(
        workflow.id,
        reviewer="fixture-reviewer",
        reason="retry must reuse the frozen release",
    )

    assert recovered.workflow.status == PromotionWorkflowStatus.APPROVED
    assert recovered.release_manifest is not None
    assert recovered.release_manifest.human_review is not None
    assert recovered.release_manifest.human_review.reason == "fixture approval"
    assert recovered.publication is not None


def test_promotion_cli_runs_fake_handoff_to_approved_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    evolution = _evolution(workspace)
    base, winner = _skill_texts(workspace, evolution)
    terms = _unique_terms(base, winner)

    begin = runner.invoke(
        app,
        [
            "skill",
            "promote",
            "begin",
            str(evolution.handoff_path),
            "--skill-name",
            "python-review-cli",
            "--target-version",
            "2.0.0-fixture",
            "--workspace",
            str(workspace),
        ],
    )
    assert begin.exit_code == 0, begin.output
    workflow_id = json.loads(begin.output)["workflow_id"]
    confirm = _final_spec(
        tmp_path,
        str(evolution.report.optimization_job_id),
        split="validation_confirm",
        terms=terms,
    )
    locked = _final_spec(
        tmp_path,
        str(evolution.report.optimization_job_id),
        split="locked_test",
        terms=terms,
    )
    for command, spec in (("confirm", confirm), ("locked", locked)):
        result = runner.invoke(
            app,
            [
                "skill",
                "promote",
                command,
                workflow_id,
                str(spec),
                "--workspace",
                str(workspace),
                "--allow-simulation",
            ],
        )
        assert result.exit_code == 0, result.output

    denied = runner.invoke(
        app,
        [
            "skill",
            "promote",
            "approve",
            workflow_id,
            "--reviewer",
            "fixture-reviewer",
            "--reason",
            "fixture approval",
            "--workspace",
            str(workspace),
        ],
    )
    assert denied.exit_code != 0
    approved = runner.invoke(
        app,
        [
            "skill",
            "promote",
            "approve",
            workflow_id,
            "--reviewer",
            "fixture-reviewer",
            "--reason",
            "fixture approval",
            "--confirm-human-review",
            "--allow-simulation",
            "--workspace",
            str(workspace),
        ],
    )
    assert approved.exit_code == 0, approved.output
    summary = json.loads(approved.output)
    assert summary["status"] == "APPROVED"
    assert summary["simulated"] is True
    assert summary["skill_version_manifest"] is not None
