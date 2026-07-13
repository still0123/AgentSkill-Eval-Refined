"""Group-aware statistics and safe static report tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    AgentSnapshot,
    ArtifactManifest,
    AttemptStatus,
    EvaluationOutcome,
    ExecutionStatus,
    ExperimentManifest,
    ExperimentStatus,
    ExperimentVariant,
    PairBlock,
    Run,
    RunAttempt,
    RunMeasurement,
    RunnerSnapshot,
    RunPlanFingerprint,
    SandboxSnapshot,
    ToolSnapshot,
    VariantReference,
    VariantRole,
)
from agentskill_eval_experiment import (
    AnalysisConfig,
    ExperimentAnalyzer,
    LocalExperimentStore,
    StaticReportWriter,
    StatisticsError,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
cli_runner = CliRunner()


def update_run(run: Run, **updates: Any) -> Run:
    payload: Dict[str, Any] = run.model_dump(mode="python", round_trip=True)
    payload.update(updates)
    return Run.model_validate(payload)


def make_variants(experiment_id: UUID) -> Tuple[ExperimentVariant, ExperimentVariant]:
    common = {
        "experiment_id": experiment_id,
        "runner_snapshot": RunnerSnapshot(
            name="mock", version="1", binary_sha256="0" * 64
        ),
        "agent_snapshot": AgentSnapshot(engine="mock", model="mock"),
        "tool_snapshot": ToolSnapshot(),
        "sandbox_snapshot": SandboxSnapshot(profile="runner_default"),
    }
    return (
        ExperimentVariant(
            id=uuid4(),
            name="control<script>alert(1)</script>",
            role=VariantRole.BASELINE,
            **common,
        ),
        ExperimentVariant(
            id=uuid4(),
            name='treatment<img src=x onerror="alert(2)">',
            role=VariantRole.TREATMENT,
            **common,
        ),
    )


def prepare_evidence(
    tmp_path: Path,
) -> Tuple[LocalExperimentStore, ExperimentManifest, ExperimentVariant, ExperimentVariant]:
    store = LocalExperimentStore(tmp_path)
    experiment_id = uuid4()
    control, treatment = make_variants(experiment_id)
    experiment = ExperimentManifest(
        id=experiment_id,
        name="<script>malicious title</script>",
        code_revision="statistics-test",
        dataset_version_id=uuid4(),
        dataset_sha256=DIGEST_A,
        protocol_snapshot={"repeats": 2},
        statistics_plan={"weighting": "equal_group"},
        budget_snapshot={"max_runs": 12},
        variants=tuple(
            VariantReference(
                variant_id=variant.id,
                variant_sha256=variant.variant_sha256,
                manifest_path=f"variants/{variant.id}.json",
            )
            for variant in (control, treatment)
        ),
        status=ExperimentStatus.FROZEN,
    )
    store.save_experiment(experiment)
    store.save_variant(control)
    store.save_variant(treatment)

    # A: win, B: tie+, C: primary tie+ with one treatment invalid.
    cases = (
        (uuid4(), "repo/group-1", ((EvaluationOutcome.FAIL, EvaluationOutcome.PASS),) * 2),
        (uuid4(), "repo/group-1", ((EvaluationOutcome.PASS, EvaluationOutcome.PASS),) * 2),
        (
            uuid4(),
            "<svg/onload=alert(3)>",
            (
                (EvaluationOutcome.PASS, EvaluationOutcome.INVALID),
                (EvaluationOutcome.PASS, EvaluationOutcome.PASS),
            ),
        ),
    )
    for case_id, group, repeats in cases:
        for repeat_index, outcomes in enumerate(repeats):
            block = PairBlock(
                id=uuid4(),
                experiment_id=experiment_id,
                case_id=case_id,
                independence_group=group,
                repeat_index=repeat_index,
                seed=repeat_index,
                execution_order=(control.id, treatment.id),
            )
            store.save_pair_block(block)
            for variant, outcome in zip((control, treatment), outcomes):
                _persist_terminal_run(store, experiment_id, block, variant, outcome)
    return store, experiment, control, treatment


def _persist_terminal_run(
    store: LocalExperimentStore,
    experiment_id: UUID,
    block: PairBlock,
    variant: ExperimentVariant,
    outcome: EvaluationOutcome,
) -> None:
    run = Run(
        id=uuid4(),
        experiment_id=experiment_id,
        pair_block_id=block.id,
        variant_id=variant.id,
        run_plan_fingerprint=RunPlanFingerprint(
            case_sha256=DIGEST_A,
            grader_sha256=DIGEST_B,
            platform_compiled_prompt_sha256="c" * 64,
            upstream_config_sha256="d" * 64,
        ),
        max_attempts=1,
    )
    store.save_run(run)
    for status in (
        ExecutionStatus.QUEUED,
        ExecutionStatus.LEASED,
        ExecutionStatus.PREPARING,
        ExecutionStatus.RUNNING,
        ExecutionStatus.GRADING,
        ExecutionStatus.PERSISTING,
    ):
        updates: Dict[str, Any] = {"execution_status": status}
        if status == ExecutionStatus.LEASED:
            updates["lease_generation"] = 1
        run = update_run(run, **updates)
        store.save_run(run)

    now = datetime.now(timezone.utc)
    invalid = outcome == EvaluationOutcome.INVALID
    attempt = RunAttempt(
        id=uuid4(),
        run_id=run.id,
        attempt_no=1,
        lease_generation=1,
        fencing_token=uuid4(),
        status=AttemptStatus.FAILED if invalid else AttemptStatus.COMPLETED,
        claimed_at=now,
        finished_at=now,
        error_code="provider_error" if invalid else None,
    )
    terminal_status = ExecutionStatus.INFRA_FAILED if invalid else ExecutionStatus.COMPLETED
    run = update_run(
        run,
        execution_status=terminal_status,
        evaluation_outcome=outcome,
        final_score=None if invalid else (1.0 if outcome == EvaluationOutcome.PASS else 0.0),
        finished_at=now,
    )
    is_treatment = variant.role == VariantRole.TREATMENT
    measurement = RunMeasurement(
        run_id=run.id,
        attempt_id=attempt.id,
        runner_status=(
            "ERROR"
            if invalid
            else ("PASS" if outcome == EvaluationOutcome.PASS else "FAIL")
        ),
        runner_exit_reason="execution_error" if invalid else "completed",
        process_exit_code=1 if invalid else 0,
        duration_ms=900 if is_treatment else 1000,
        input_tokens=50 if is_treatment else 40,
        output_tokens=70 if is_treatment else 60,
        cost_microusd=12 if is_treatment else 10,
    )
    store.commit_attempt(run, attempt, ArtifactManifest(), measurement)


def test_group_weighted_statistics_invalid_sensitivity_and_efficiency(tmp_path: Path) -> None:
    store, experiment, control, treatment = prepare_evidence(tmp_path)
    config = AnalysisConfig(
        control_variant_id=control.id,
        treatment_variant_id=treatment.id,
        bootstrap_resamples=300,
        bootstrap_seed=7,
        min_independent_groups=2,
    )
    analyzer = ExperimentAnalyzer(store)
    result = analyzer.analyze(experiment.id, config)
    repeated = analyzer.analyze(experiment.id, config)

    assert result == repeated
    assert result.run_count == 12
    assert result.case_count == 3
    assert result.independence_group_count == 2
    assert result.complete_block_ratio == 1
    assert result.valid_block_ratio == pytest.approx(5 / 6)
    assert result.inference_ready is True

    primary = result.primary_assignment_based
    assert primary.control_pass_rate == pytest.approx(0.75)
    assert primary.treatment_pass_rate == pytest.approx(0.75)
    assert primary.absolute_gain == pytest.approx(0)
    assert primary.relative_gain == pytest.approx(0)
    assert primary.gain_ci is not None

    capability = result.sensitivity_capability
    assert capability.control_pass_rate == pytest.approx(0.75)
    assert capability.treatment_pass_rate == pytest.approx(1.0)
    assert capability.absolute_gain == pytest.approx(0.25)
    assert result.wtl.model_dump() == {
        "win": 1,
        "tie_positive": 2,
        "tie_negative": 0,
        "loss": 0,
    }
    assert result.variants[1].invalid_runs == 1
    assert result.tokens.control_mean == pytest.approx(100)
    assert result.tokens.treatment_mean == pytest.approx(120)
    assert result.tokens.relative_overhead == pytest.approx(0.2)
    assert result.tokens.paired_median_delta == pytest.approx(20)
    assert result.tokens.relative_overhead_ci is not None
    assert result.tokens.paired_median_delta_ci is not None
    assert result.tokens.observed_pairs == 3
    assert result.latency_ms.relative_overhead == pytest.approx(-0.1)
    assert result.cost_microusd.relative_overhead == pytest.approx(0.2)
    assert result.variants[0].cost_per_success_microusd == pytest.approx(15)
    assert result.variants[1].cost_per_success_microusd == pytest.approx(14.4)


def test_static_report_is_offline_escaped_and_machine_readable(tmp_path: Path) -> None:
    store, experiment, control, treatment = prepare_evidence(tmp_path)
    statistics_result = ExperimentAnalyzer(store).analyze(
        experiment.id,
        AnalysisConfig(control.id, treatment.id, bootstrap_resamples=50),
    )
    paths = StaticReportWriter(store).write(experiment.id, statistics_result)

    document = paths.html_path.read_text(encoding="utf-8")
    assert "<script>malicious" not in document
    assert "<img src=x" not in document
    assert "<svg/onload" not in document
    assert "&lt;script&gt;malicious title&lt;/script&gt;" in document
    assert "default-src 'none'" in document
    assert "<script" not in document.lower()
    assert "src=\"http" not in document.lower()
    assert "artifacts/manifest.json" in document

    machine = json.loads(paths.json_path.read_text(encoding="utf-8"))
    assert machine["report_schema_version"] == "ase/report/v1alpha1"
    assert machine["statistics"]["run_count"] == 12
    assert machine["statistics"]["primary_assignment_based"]["absolute_gain"] == 0

    cli_result = cli_runner.invoke(
        app,
        [
            "report",
            "generate",
            str(tmp_path),
            str(experiment.id),
            "--control",
            str(control.id),
            "--treatment",
            str(treatment.id),
            "--bootstrap-resamples",
            "10",
        ],
    )
    assert cli_result.exit_code == 0
    cli_payload = json.loads(cli_result.stdout)
    assert cli_payload["inference_ready"] is True
    assert cli_payload["html_report"].endswith("report.html")


def test_statistics_reject_nonterminal_runs(tmp_path: Path) -> None:
    store, experiment, control, treatment = prepare_evidence(tmp_path)
    run = store.list_runs(experiment.id)[0]
    # A separate store with an incomplete plan is covered by the explicit terminal check.
    payload = run.model_dump(mode="python", round_trip=True)
    payload.update(
        {
            "execution_status": ExecutionStatus.PERSISTING,
            "evaluation_outcome": None,
            "final_score": None,
            "finished_at": None,
        }
    )
    # Direct replacement simulates an interrupted worker while preserving a valid envelope.
    from agentskill_eval_experiment.storage.manifests import model_bytes

    layout_path = store.workspace / "experiments" / str(experiment.id) / "runs" / str(run.id)
    store.writer.write(layout_path / "run.json", model_bytes(Run.model_validate(payload)))

    with pytest.raises(StatisticsError, match="not terminal"):
        ExperimentAnalyzer(store).analyze(
            experiment.id,
            AnalysisConfig(control.id, treatment.id, bootstrap_resamples=10),
        )


def test_relative_gain_is_na_when_control_rate_is_zero(tmp_path: Path) -> None:
    analyzer = ExperimentAnalyzer(LocalExperimentStore(tmp_path))
    summary = analyzer._estimand(
        {uuid4(): ("one-group", 0.0, 1.0)},
        AnalysisConfig(uuid4(), uuid4(), bootstrap_resamples=10),
    )
    assert summary.absolute_gain == 1
    assert summary.relative_gain is None
