from __future__ import annotations

import asyncio
import sys
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from agentskill_eval_benchmark_gen import DatasetLoader
from agentskill_eval_contracts import (
    AgentSnapshot,
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
    AnalysisConfig,
    CaseExecutionSpec,
    ExperimentAnalyzer,
    ExperimentLayout,
    LocalExperimentExecutor,
    LocalExperimentPlanner,
    LocalExperimentStore,
    StaticReportWriter,
    VariantRuntimeSpec,
)
from agentskill_eval_runner_adapters import (
    RunnerRequest,
    RunnerStatus,
    SkillUpRunnerAdapter,
    discover_skill_up_binary,
)

ROOT = Path(__file__).resolve().parents[2]
BINARY = discover_skill_up_binary()


@pytest.mark.skipif(BINARY is None, reason="pinned skill-up binary is not installed")
def test_pinned_skill_up_custom_engine_contract(tmp_path: Path) -> None:
    assert BINARY is not None
    eval_dir = ROOT / "tests/fixtures/runner_eval"
    code = (
        "import json,sys; json.load(open(sys.argv[1])); "
        "print(json.dumps({'exit_code':0,'final_message':'custom-engine-handled',"
        "'turns':1,'input_tokens':7,'output_tokens':3}))"
    )
    request = RunnerRequest(
        execution_id="real-skill-up",
        case_id="golden-pass",
        variant="baseline",
        source_eval_dir=eval_dir,
        case_file=eval_dir / "cases/golden-pass.yaml",
        run_dir=tmp_path,
        engine={
            "name": "golden-local",
            "custom": {
                "transport": "local",
                "response_format": "session_result",
                "local": {
                    "command": sys.executable,
                    "args": ["-c", code, "${input_file}"],
                },
            },
        },
        environment={"type": "none"},
        timeout_seconds=30,
        max_turns=3,
    )
    adapter = SkillUpRunnerAdapter(BINARY)

    async def scenario() -> None:
        validation = await adapter.validate(request)
        assert validation.valid, validation.stderr
        result = await adapter.execute(request)
        assert result.status == RunnerStatus.PASS, result.stderr
        assert result.final_message == "custom-engine-handled"
        assert result.input_tokens == 7
        assert result.output_tokens == 3
        assert any(item.path == "result.json" for item in result.artifacts)

    asyncio.run(scenario())


@pytest.mark.skipif(BINARY is None, reason="pinned skill-up binary is not installed")
def test_demo_dataset_all_cases_validate_with_pinned_skill_up(tmp_path: Path) -> None:
    assert BINARY is not None
    dataset = DatasetLoader().load(ROOT / "examples/datasets/python-review-demo")
    skill = ROOT / "examples/skills/python-review-v1"
    code = "import json,sys; json.load(open(sys.argv[1])); print('{}')"
    engine = {
        "name": "demo-contract-local",
        "custom": {
            "transport": "local",
            "response_format": "session_result",
            "local": {
                "command": sys.executable,
                "args": ["-c", code, "${input_file}"],
            },
        },
    }
    adapter = SkillUpRunnerAdapter(BINARY)

    async def scenario() -> None:
        for index, case in enumerate(dataset.execution_specs()):
            request = RunnerRequest(
                execution_id=f"demo-validate-{index}",
                case_id=case.runner_case_id,
                variant="with-skill",
                source_eval_dir=case.source_eval_dir,
                case_file=case.case_file,
                run_dir=tmp_path / case.runner_case_id,
                engine=engine,
                environment={"type": "none"},
                skill_path=skill,
                timeout_seconds=30,
                max_turns=4,
            )
            request.run_dir.mkdir()
            validation = await adapter.validate(request)
            assert validation.valid, (
                case.runner_case_id,
                validation.errors,
                validation.stdout,
                validation.stderr,
            )

    asyncio.run(scenario())


@pytest.mark.skipif(BINARY is None, reason="pinned skill-up binary is not installed")
def test_paired_executor_runs_both_arms_with_real_skill_up(tmp_path: Path) -> None:
    assert BINARY is not None
    experiment_id = uuid4()
    runner = RunnerSnapshot(
        name="skill-up",
        version="0.5.0",
        binary_sha256=sha256(BINARY.read_bytes()).hexdigest(),
    )
    common = {
        "experiment_id": experiment_id,
        "runner_snapshot": runner,
        "agent_snapshot": AgentSnapshot(engine="golden-local", model="deterministic"),
        "tool_snapshot": ToolSnapshot(),
        "sandbox_snapshot": SandboxSnapshot(profile="runner_default"),
    }
    baseline = ExperimentVariant(
        id=uuid4(), name="without-skill", role=VariantRole.BASELINE, **common
    )
    treatment = ExperimentVariant(
        id=uuid4(),
        name="with-skill",
        role=VariantRole.TREATMENT,
        skill_snapshot=SkillSnapshot(
            skill_id=uuid4(),
            version_id=uuid4(),
            name="golden-selected",
            version="1.0.0",
            content_sha256="b" * 64,
            injection_mode="native_install",
        ),
        **common,
    )
    arms = (baseline, treatment)
    experiment = ExperimentManifest(
        id=experiment_id,
        name="real-paired-runner",
        code_revision="integration-test",
        dataset_version_id=uuid4(),
        dataset_sha256="a" * 64,
        protocol_snapshot={"repeats": 1, "random_seed": 42},
        statistics_plan={"primary": "absolute_gain"},
        budget_snapshot={"max_runs": 2},
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
    case_file = ROOT / "tests/fixtures/runner_eval/cases/golden-pass.yaml"
    case = CaseExecutionSpec(
        id=uuid4(),
        runner_case_id="golden-pass",
        independence_group="repo/golden",
        source_eval_dir=case_file.parents[1],
        case_file=case_file,
        case_sha256=sha256(case_file.read_bytes()).hexdigest(),
        grader_sha256=sha256(b"expect").hexdigest(),
        platform_compiled_prompt_sha256=sha256(b"prompt").hexdigest(),
    )
    skill = tmp_path / "selected-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: golden-selected\n---\n# Golden selected\n", encoding="utf-8"
    )
    code = (
        "import json,sys; json.load(open(sys.argv[1])); "
        "print(json.dumps({'exit_code':0,'final_message':'custom-engine-handled',"
        "'turns':1,'input_tokens':7,'output_tokens':3}))"
    )
    engine = {
        "name": "golden-local",
        "custom": {
            "transport": "local",
            "response_format": "session_result",
            "local": {"command": sys.executable, "args": ["-c", code, "${input_file}"]},
        },
    }
    runtimes = (
        VariantRuntimeSpec(baseline.id, engine, {"type": "none"}),
        VariantRuntimeSpec(treatment.id, engine, {"type": "none"}, skill_path=skill),
    )
    store = LocalExperimentStore(tmp_path / "workspace")
    planner = LocalExperimentPlanner(store)
    plan = planner.build(experiment, arms, runtimes, [case], repeats=1, random_seed=42)
    planner.persist(plan)

    summary = asyncio.run(
        LocalExperimentExecutor(store, SkillUpRunnerAdapter(BINARY)).execute(plan)
    )

    assert summary.completed_runs == 2
    assert summary.invalid_runs == 0
    assert all(record.runner_status == RunnerStatus.PASS for record in summary.records)
    layout = ExperimentLayout(store.workspace, experiment_id)
    for block in plan.blocks:
        for run in block.runs:
            assert layout.artifact_manifest(run.id, 1).is_file()
            assert (layout.raw_runner(run.id, 1) / "result.json").is_file()
            activation = store.load_activation_evidence(experiment_id, run.id, 1)
            security = store.load_security_scan(experiment_id, run.id, 1)
            trace = store.load_trace_manifest(experiment_id, run.id, 1)
            diagnosis = store.load_failure_diagnosis(experiment_id, run.id, 1)
            if run.variant_id == baseline.id:
                assert activation.skill_expected is False
                assert activation.installed is False
                assert activation.baseline_clean is True
            else:
                assert activation.skill_expected is True
                assert activation.installed is True
                assert activation.installed_skill_sha256 is not None
            assert activation.compiled_eval_sha256 is not None
            assert security.status == "clean"
            assert diagnosis.status == "no_failure"
            capabilities = {item.name: item for item in trace.capabilities}
            assert capabilities["runner_lifecycle"].availability.value == "observed"
            assert capabilities["tool_file_command"].availability.value == "unavailable"

    statistics = ExperimentAnalyzer(store).analyze(
        experiment_id,
        AnalysisConfig(
            baseline.id,
            treatment.id,
            bootstrap_resamples=10,
            min_independent_groups=1,
        ),
    )
    assert statistics.primary_assignment_based.absolute_gain == 0
    assert statistics.tokens.control_mean == 10
    assert statistics.tokens.treatment_mean == 10
    report = StaticReportWriter(store).write(experiment_id, statistics)
    assert report.html_path.is_file()
    assert report.json_path.is_file()
