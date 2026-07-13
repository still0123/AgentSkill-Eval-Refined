"""Planning and execution tests for the local paired-experiment loop."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Tuple
from uuid import UUID, uuid4

import pytest

from agentskill_eval_contracts import (
    AgentSnapshot,
    AttemptStatus,
    EvaluationOutcome,
    ExecutionStatus,
    ExperimentManifest,
    ExperimentStatus,
    ExperimentVariant,
    RunnerSnapshot,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
    VariantReference,
    VariantRole,
)
from agentskill_eval_experiment import (
    CaseExecutionSpec,
    ExperimentLayout,
    LocalExperimentExecutor,
    LocalExperimentPlanner,
    LocalExperimentStore,
    VariantRuntimeSpec,
)
from agentskill_eval_runner_adapters import (
    ExitReason,
    MockRunnerAdapter,
    RunnerResult,
    RunnerStatus,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
ROOT = Path(__file__).resolve().parents[2]


def variants(experiment_id: UUID) -> Tuple[ExperimentVariant, ExperimentVariant]:
    common = {
        "experiment_id": experiment_id,
        "runner_snapshot": RunnerSnapshot(
            name="mock",
            version="1",
            binary_sha256="0" * 64,
        ),
        "agent_snapshot": AgentSnapshot(engine="mock", model="mock-v1"),
        "tool_snapshot": ToolSnapshot(),
        "sandbox_snapshot": SandboxSnapshot(profile="runner_default"),
    }
    baseline = ExperimentVariant(
        id=uuid4(),
        name="without-skill",
        role=VariantRole.BASELINE,
        **common,
    )
    treatment = ExperimentVariant(
        id=uuid4(),
        name="with-skill",
        role=VariantRole.TREATMENT,
        skill_snapshot=SkillSnapshot(
            skill_id=uuid4(),
            version_id=uuid4(),
            name="selected",
            version="1.0.0",
            content_sha256=DIGEST_B,
            injection_mode="native_install",
        ),
        **common,
    )
    return baseline, treatment


def manifest(
    experiment_id: UUID,
    arms: Tuple[ExperimentVariant, ExperimentVariant],
    *,
    max_runs: int = 20,
) -> ExperimentManifest:
    return ExperimentManifest(
        id=experiment_id,
        name="paired-test",
        code_revision="test-revision",
        dataset_version_id=uuid4(),
        dataset_sha256=DIGEST_A,
        protocol_snapshot={"repeats": 2, "random_seed": 17},
        statistics_plan={"primary": "absolute_gain"},
        budget_snapshot={"max_runs": max_runs},
        variants=tuple(
            VariantReference(
                variant_id=arm.id,
                variant_sha256=arm.variant_sha256,
                manifest_path=f"variants/{arm.id}.json",
            )
            for arm in arms
        ),
        status=ExperimentStatus.FROZEN,
    )


def case_spec() -> CaseExecutionSpec:
    case_file = ROOT / "tests/fixtures/runner_eval/cases/golden-pass.yaml"
    return CaseExecutionSpec(
        id=uuid4(),
        runner_case_id="golden-pass",
        independence_group="repo/golden",
        source_eval_dir=case_file.parents[1],
        case_file=case_file,
        case_sha256=sha256(case_file.read_bytes()).hexdigest(),
        grader_sha256=sha256(b"expect:custom-engine-handled").hexdigest(),
        platform_compiled_prompt_sha256=sha256(b"deterministic prompt").hexdigest(),
    )


def runtime_specs(
    arms: Tuple[ExperimentVariant, ExperimentVariant],
    skill: Path,
) -> Tuple[VariantRuntimeSpec, VariantRuntimeSpec]:
    return (
        VariantRuntimeSpec(
            variant_id=arms[0].id,
            engine={"name": "mock"},
            environment={"type": "none"},
        ),
        VariantRuntimeSpec(
            variant_id=arms[1].id,
            engine={"name": "mock"},
            environment={"type": "none"},
            skill_path=skill,
        ),
    )


def create_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Selected\n", encoding="utf-8")
    return skill


def test_planner_is_deterministic_budgeted_and_idempotent(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path / "workspace")
    experiment_id = uuid4()
    arms = variants(experiment_id)
    experiment = manifest(experiment_id, arms)
    case = case_spec()
    runtimes = runtime_specs(arms, create_skill(tmp_path))
    planner = LocalExperimentPlanner(store)

    first = planner.build(experiment, arms, runtimes, [case], repeats=2, random_seed=17)
    second = planner.build(experiment, arms, runtimes, [case], repeats=2, random_seed=17)

    assert [item.block.block_sha256 for item in first.blocks] == [
        item.block.block_sha256 for item in second.blocks
    ]
    assert len(first.blocks) == 2
    assert all(len(item.runs) == 2 for item in first.blocks)
    assert all(
        tuple(run.variant_id for run in item.runs) == item.block.execution_order
        for item in first.blocks
    )
    planner.persist(first)
    planner.persist(first)
    layout = ExperimentLayout(store.workspace, experiment_id)
    case_manifest = store.load_frozen_input(experiment_id, "case_source", case.id)
    skill_manifest = store.load_frozen_input(experiment_id, "skill", arms[1].id)
    assert case_manifest.files
    assert skill_manifest.files[0].path == "SKILL.md"
    assert (layout.frozen_input_files("case_source", case.id) / "cases/golden-pass.yaml").is_file()
    assert (layout.frozen_input_files("skill", arms[1].id) / "SKILL.md").read_text() == (
        "# Selected\n"
    )
    for block in first.blocks:
        for run in block.runs:
            persisted = store.load_run(experiment_id, run.id)
            assert persisted.execution_status == ExecutionStatus.QUEUED
            assert persisted.queued_at is not None

    too_small = manifest(experiment_id, arms, max_runs=1)
    with pytest.raises(ValueError, match="budget"):
        planner.build(too_small, arms, runtimes, [case], repeats=1, random_seed=17)

    with pytest.raises(ValueError, match="case IDs"):
        planner.build(experiment, arms, runtimes, [case, case], repeats=1, random_seed=17)


def test_executor_persists_paired_outcomes_attempts_and_order(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path / "workspace")
    experiment_id = uuid4()
    arms = variants(experiment_id)
    experiment = manifest(experiment_id, arms)
    planner = LocalExperimentPlanner(store)
    plan = planner.build(
        experiment,
        arms,
        runtime_specs(arms, create_skill(tmp_path)),
        [case_spec()],
        repeats=1,
        random_seed=9,
    )
    planner.persist(plan)
    results = {
        f"golden-pass:{arms[0].id}": RunnerResult(
            execution_id="configured",
            case_id="golden-pass",
            status=RunnerStatus.FAIL,
            exit_reason=ExitReason.CASE_FAILED,
            process_exit_code=1,
            grading={"score": 0.25},
        ),
        f"golden-pass:{arms[1].id}": RunnerResult(
            execution_id="configured",
            case_id="golden-pass",
            status=RunnerStatus.PASS,
            exit_reason=ExitReason.COMPLETED,
            process_exit_code=0,
        ),
    }
    progress = []
    executor = LocalExperimentExecutor(
        store,
        MockRunnerAdapter(results),
        progress_sink=lambda record, completed, total: progress.append(
            (record.run_id, completed, total)
        ),
    )
    summary = asyncio.run(executor.execute(plan))

    assert [record.variant_id for record in summary.records] == list(
        plan.blocks[0].block.execution_order
    )
    assert summary.completed_runs == 2
    assert summary.invalid_runs == 0
    assert [item[1:] for item in progress] == [(1, 2), (2, 2)]
    by_variant = {record.variant_id: record for record in summary.records}
    assert by_variant[arms[0].id].evaluation_outcome == EvaluationOutcome.FAIL
    assert by_variant[arms[1].id].evaluation_outcome == EvaluationOutcome.PASS

    for planned_run in plan.blocks[0].runs:
        run = store.load_run(experiment_id, planned_run.id)
        attempt = store.load_attempt(experiment_id, run.id, 1)
        measurement = store.load_measurement(experiment_id, run.id, 1)
        activation = store.load_activation_evidence(experiment_id, run.id, 1)
        security = store.load_security_scan(experiment_id, run.id, 1)
        assert run.execution_status == ExecutionStatus.COMPLETED
        assert run.active_attempt_id == attempt.id
        assert run.selected_attempt_sha256 is not None
        assert attempt.status == AttemptStatus.COMPLETED
        assert measurement.attempt_id == attempt.id
        assert activation.attempt_id == attempt.id
        assert activation.installed is None
        assert security.status == "clean"
        layout = ExperimentLayout(store.workspace, experiment_id)
        assert layout.artifact_manifest(run.id, 1).is_file()

    # A completed plan is idempotent: re-execution returns persisted outcomes.
    replay = asyncio.run(executor.execute(plan))
    assert [record.evaluation_outcome for record in replay.records] == [
        record.evaluation_outcome for record in summary.records
    ]


def test_validation_failure_is_invalid_not_task_failure(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path / "workspace")
    experiment_id = uuid4()
    arms = variants(experiment_id)
    experiment = manifest(experiment_id, arms)
    missing_skill = tmp_path / "missing-skill"
    planner = LocalExperimentPlanner(store)
    plan = planner.build(
        experiment,
        arms,
        runtime_specs(arms, missing_skill),
        [case_spec()],
        repeats=1,
        random_seed=1,
    )
    planner.persist(plan)
    summary = asyncio.run(LocalExperimentExecutor(store, MockRunnerAdapter()).execute(plan))

    treatment = next(record for record in summary.records if record.variant_id == arms[1].id)
    assert treatment.execution_status == ExecutionStatus.INFRA_FAILED
    assert treatment.evaluation_outcome == EvaluationOutcome.INVALID
    treatment_run = next(run for run in plan.blocks[0].runs if run.variant_id == arms[1].id)
    attempt = store.load_attempt(experiment_id, treatment_run.id, 1)
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.error_code == "runner_validation_failed"


def test_executor_uses_frozen_skill_after_original_changes(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path / "workspace")
    experiment_id = uuid4()
    arms = variants(experiment_id)
    skill = create_skill(tmp_path)
    planner = LocalExperimentPlanner(store)
    plan = planner.build(
        manifest(experiment_id, arms),
        arms,
        runtime_specs(arms, skill),
        [case_spec()],
        repeats=1,
        random_seed=11,
    )
    planner.persist(plan)
    (skill / "SKILL.md").write_text("# Mutated after freeze\n", encoding="utf-8")

    summary = asyncio.run(LocalExperimentExecutor(store, MockRunnerAdapter()).execute(plan))

    assert summary.completed_runs == 2
    frozen = ExperimentLayout(store.workspace, experiment_id).frozen_input_files(
        "skill", arms[1].id
    )
    assert (frozen / "SKILL.md").read_text(encoding="utf-8") == "# Selected\n"


def test_secret_in_runner_output_is_blocked_before_any_bytes_are_persisted(
    tmp_path: Path,
) -> None:
    store = LocalExperimentStore(tmp_path / "workspace")
    experiment_id = uuid4()
    arms = variants(experiment_id)
    skill = create_skill(tmp_path)
    secret = "token-that-must-never-be-persisted"
    runtimes = (
        VariantRuntimeSpec(arms[0].id, {"name": "mock"}, {"type": "none"}),
        VariantRuntimeSpec(
            arms[1].id,
            {"name": "mock"},
            {"type": "none"},
            skill_path=skill,
            secret_env={"API_TOKEN": secret},
        ),
    )
    planner = LocalExperimentPlanner(store)
    plan = planner.build(
        manifest(experiment_id, arms), arms, runtimes, [case_spec()], repeats=1, random_seed=5
    )
    planner.persist(plan)
    leaking = RunnerResult(
        execution_id="configured",
        case_id="golden-pass",
        status=RunnerStatus.PASS,
        exit_reason=ExitReason.COMPLETED,
        process_exit_code=0,
        stdout=f"leaked={secret}",
    )
    results = {f"golden-pass:{arms[1].id}": leaking}

    summary = asyncio.run(LocalExperimentExecutor(store, MockRunnerAdapter(results)).execute(plan))

    treatment_record = next(item for item in summary.records if item.variant_id == arms[1].id)
    assert treatment_record.execution_status == ExecutionStatus.INFRA_FAILED
    treatment_run = next(item for item in plan.blocks[0].runs if item.variant_id == arms[1].id)
    attempt = store.load_attempt(experiment_id, treatment_run.id, 1)
    scan = store.load_security_scan(experiment_id, treatment_run.id, 1)
    assert attempt.error_code == "secret_leak_detected"
    assert scan.status == "blocked"
    assert scan.matched_secret_names == ("API_TOKEN",)
    runtime_attempt = (
        store.workspace / ".runtime" / str(experiment_id) / str(treatment_run.id) / "1"
    )
    assert not runtime_attempt.exists()
    secret_bytes = secret.encode()
    for path in store.workspace.rglob("*"):
        if path.is_file():
            assert secret_bytes not in path.read_bytes(), path
