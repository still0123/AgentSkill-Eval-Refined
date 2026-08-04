"""Stage 3C orchestration tests; no provider or paid Agent is invoked."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    CandidateEvaluation,
    FailureLabel,
    SearchCaseResult,
    SearchEvaluationStage,
)
from agentskill_eval_real_evidence import (
    AgentSpec,
    PricingSpec,
    RealAgentEvidenceSpec,
    RunnerSpec,
)
from agentskill_eval_skill_optimizer import (
    BudgetedRealEvolutionExecutor,
    DatasetPlanDescriptor,
    EvolutionDatasetPlan,
    EvolutionExecutionPlan,
    EvolutionRuntimeError,
    EvolutionRuntimeSpec,
    ImprovementHypothesis,
    RealEvaluationAuthorization,
    RegressionGateResult,
    SearchAlgorithmSpec,
    SkillSearchError,
    StageBudgetCap,
)
from agentskill_eval_skill_optimizer.evolution import HypothesisArtifact
from agentskill_eval_skill_optimizer.execution_plan import (
    EvolutionStagePlan,
    FrozenAgentIdentity,
    PlannedTokenUsage,
)
from agentskill_eval_skill_optimizer.search import OptimizationStore


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _evaluation(dataset_sha: str) -> CandidateEvaluation:
    results = tuple(
        SearchCaseResult(
            case_id=f"case-{index}",
            passed=True,
            score=1,
            input_tokens=10,
            output_tokens=2,
            latency_ms=5,
            cost_microusd=1,
            outcome="pass",
            experiment_id=uuid4(),
            run_id=uuid4(),
            attempt_id=uuid4(),
        )
        for index in range(4)
    )
    return CandidateEvaluation(
        stage=SearchEvaluationStage.REGRESSION_DEV,
        dataset_sha256=dataset_sha,
        evaluator_sha256="e" * 64,
        case_ids=tuple(item.case_id for item in results),
        results=results,
        pass_rate=1,
        mean_score=1,
        total_tokens=48,
        total_latency_ms=20,
        total_cost_microusd=4,
        simulated=False,
        evaluated_at=datetime.now(timezone.utc),
    )


def _invalid_evaluation(dataset_sha: str) -> CandidateEvaluation:
    results = list(_evaluation(dataset_sha).results)
    results[0] = SearchCaseResult(
        case_id="case-0",
        passed=False,
        score=0,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        cost_microusd=0,
        outcome="invalid",
        experiment_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
    )
    return CandidateEvaluation(
        stage=SearchEvaluationStage.REGRESSION_DEV,
        dataset_sha256=dataset_sha,
        evaluator_sha256="e" * 64,
        case_ids=tuple(item.case_id for item in results),
        results=tuple(results),
        pass_rate=0.75,
        mean_score=0.75,
        total_tokens=36,
        total_latency_ms=15,
        total_cost_microusd=3,
        simulated=False,
        evaluated_at=datetime.now(timezone.utc),
    )


def test_regression_gate_rejects_invalid_observations() -> None:
    dataset_sha = "2" * 64
    evaluation = _invalid_evaluation(dataset_sha)
    with pytest.raises(ValueError, match="decision"):
        RegressionGateResult(
            dataset_sha256=dataset_sha,
            base=evaluation,
            winner=evaluation,
            loss_cases=(),
            invalid_cases=("case-0",),
            token_overhead_ratio=0,
            max_loss_cases=0,
            max_token_overhead_ratio=0.25,
            passed=True,
        )
    gate = RegressionGateResult(
        dataset_sha256=dataset_sha,
        base=evaluation,
        winner=evaluation,
        loss_cases=(),
        invalid_cases=("case-0",),
        token_overhead_ratio=0,
        max_loss_cases=0,
        max_token_overhead_ratio=0.25,
        passed=False,
    )
    assert gate.invalid_cases == ("case-0",)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[EvolutionRuntimeSpec, Path]:
    base = tmp_path / "base"
    manual = tmp_path / "manual"
    base.mkdir()
    manual.mkdir()
    (base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    (manual / "SKILL.md").write_text("# Manual\n", encoding="utf-8")
    executable = tmp_path / "fake-executable"
    executable.write_text("fixture", encoding="utf-8")
    real_spec = RealAgentEvidenceSpec(
        schema_version="ase/real-agent-evidence/v1alpha1",
        name="stage-3c-fake-process-agent",
        dataset_path=tmp_path,
        skill_path=base,
        case_ids=("case-0", "case-1"),
        evidence_class="observed_agent",
        simulated=False,
        runner=RunnerSpec(
            path=executable,
            expected_sha256=_sha(executable.read_bytes()),
            expected_version="fixture-runner 1",
        ),
        agent=AgentSpec(
            path=executable,
            expected_sha256=_sha(executable.read_bytes()),
            expected_version="fixture-agent 1",
            engine="fake-process",
            engine_version="1",
            provider="fake-provider",
            model="fake-model",
            temperature=0,
            seed=7,
            max_turns=4,
            max_tool_calls=8,
            timeout_seconds=5,
            tool_capabilities=("filesystem", "shell", "tests"),
            secret_env_names=("FAKE_API_KEY",),
            max_input_tokens=1000,
            max_output_tokens=100,
        ),
        pricing=PricingSpec(
            input_microusd_per_million_tokens=1,
            output_microusd_per_million_tokens=1,
            estimated_input_tokens_per_run=100,
            estimated_output_tokens_per_run=10,
        ),
    )
    real_config = tmp_path / "real-agent.yaml"
    real_config.write_text(
        yaml.safe_dump(real_spec.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    proposal_dir = tmp_path / "proposal"
    proposal_dir.mkdir()
    proposal_manifest = b'{"fixture":"proposal"}\n'
    (proposal_dir / "proposal-manifest.json").write_bytes(proposal_manifest)
    hypotheses = tuple(
        ImprovementHypothesis(
            id=f"candidate-{index}",
            failure_label=FailureLabel.VERIFICATION,
            hypothesis=f"Candidate hypothesis number {index} improves verification.",
            instruction=f"Apply verification instruction number {index} before completion.",
            evidence_refs=(f"diagnosis://fixture/rule-{index}",),
        )
        for index in range(3)
    )
    proposals_path = proposal_dir / "proposals.json"
    proposals_path.write_text(
        HypothesisArtifact(generator="fixture", hypotheses=hypotheses).model_dump_json(),
        encoding="utf-8",
    )
    dataset_hashes = {
        "validation_search": "1" * 64,
        "regression_dev": "2" * 64,
        "validation_confirm": "3" * 64,
        "locked_test": "4" * 64,
    }
    datasets = EvolutionDatasetPlan(
        **{
            split: DatasetPlanDescriptor(
                split=split,
                dataset_version_sha256=digest,
                split_plan_sha256="f" * 64,
                case_count=4,
                independent_group_count=4,
            )
            for split, digest in dataset_hashes.items()
        }
    )
    search = SearchAlgorithmSpec(
        subset_size=2, promote_search_candidates=1, random_seed=2026
    )
    stage_counts = {
        "validation_search": (20, 40),
        "regression_dev": (8, 16),
        "validation_confirm": (8, 16),
        "locked_test": (8, 16),
    }
    stages = tuple(
        EvolutionStagePlan(
            stage=stage,
            ordinal=index,
            dataset_version_sha256=dataset_hashes[stage],
            case_count=4,
            candidate_case_evaluations=evaluations,
            agent_runs=runs,
            estimated_cost_microusd=runs,
            estimated_tokens=PlannedTokenUsage(
                input_tokens=runs * 100, cache_hit_tokens=0, output_tokens=runs * 10
            ),
            budget_cap=StageBudgetCap(max_agent_runs=runs, max_cost_microusd=runs),
            locked_receipt_required=stage == "locked_test",
        )
        for index, (stage, (evaluations, runs)) in enumerate(stage_counts.items(), 1)
    )
    proposal_job_id = uuid4()
    plan = EvolutionExecutionPlan(
        plan_id=uuid4(),
        name="stage-3c-fixture",
        proposal_job_id=proposal_job_id,
        proposal_manifest_sha256=_sha(proposal_manifest),
        proposal_input_evidence_class="simulated_fixture",
        base_skill_sha256=_sha((base / "SKILL.md").read_bytes()),
        proposal_count=3,
        total_candidate_count=6,
        agent=FrozenAgentIdentity(
            provider="fake-provider",
            model="fake-model",
            engine="fake-process",
            engine_version="1",
            agent_executable_sha256=real_spec.agent.expected_sha256,
            runner_name="skill-up",
            runner_version=real_spec.runner.expected_version,
            runner_executable_sha256=real_spec.runner.expected_sha256,
            temperature=0,
            seed=7,
            max_turns=4,
            timeout_seconds=5,
            tool_capabilities=("filesystem", "shell", "tests"),
            sandbox_profile="runner_default",
            network_policy="provider_only",
            config_sha256=_sha(real_config.read_bytes()),
        ),
        datasets=datasets,
        stages=stages,
        total_agent_runs=88,
        total_estimated_cost_microusd=88,
        execution_order=("validation_search", "regression_dev"),
        capability_requirements=("separate authorization",),
        claim_limit="Fixture-only adaptive execution.",
    )
    plan_dir = tmp_path / "plan"
    dry_dir = tmp_path / "dry"
    benchmark = tmp_path / "benchmark"
    plan_dir.mkdir()
    dry_dir.mkdir()
    benchmark.mkdir()
    plan_path = plan_dir / "execution-plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    for split in ("validation_search", "regression_dev"):
        (benchmark / split).mkdir()
    bindings = tuple(
        SimpleNamespace(
            split=split,
            relative_path=split,
            dataset_version_sha256=dataset_hashes[split],
        )
        for split in ("validation_search", "regression_dev")
    )
    dry_report = SimpleNamespace(
        dry_run_id=uuid4(),
        execution_plan_id=plan.plan_id,
        execution_plan_sha256=_sha(plan_path.read_bytes()),
        adaptive_bindings=bindings,
    )
    plan_result = SimpleNamespace(plan=plan, plan_path=plan_path)
    dry_result = SimpleNamespace(report=dry_report)
    proposal_result = SimpleNamespace(
        manifest=SimpleNamespace(
            proposal_job_id=proposal_job_id,
            proposals_sha256="a" * 64,
        ),
        proposals_path=proposals_path,
    )
    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_runtime.RealEvolutionExecutionPlanner.verify",
        lambda _self, _path: plan_result,
    )
    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_runtime.EvolutionDryRunOrchestrator.verify",
        lambda _self, _path: dry_result,
    )
    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_runtime.RealLLMProposalService.verify",
        lambda _self, _path: proposal_result,
    )

    def fake_load(root: Path) -> SimpleNamespace:
        split = root.name
        return SimpleNamespace(
            # Published DatasetVersion content hash differs from the loader's
            # metadata digest; the runtime must validate the former.
            dataset_sha256="3" * 64,
            dataset_version=SimpleNamespace(content_sha256=dataset_hashes[split]),
            cases=tuple(
                SimpleNamespace(metadata=SimpleNamespace(split=split, case_id=f"case-{i}"))
                for i in range(4)
            ),
        )

    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_runtime.DatasetLoader.load",
        lambda _self, root: fake_load(root),
    )
    spec = EvolutionRuntimeSpec(
        schema_version="ase/real-evolution-runtime-spec/v1alpha1",
        name="stage-3c-fixture",
        execution_plan_directory=plan_dir,
        dry_run_directory=dry_dir,
        benchmark_workspace=benchmark,
        proposal_directory=proposal_dir,
        base_skill_path=base,
        manual_skill_path=manual,
        real_agent_config_path=real_config,
        search=search,
        claim_limit="Fixture-only adaptive execution.",
    )
    return spec, tmp_path / "winner-SKILL.md"


def test_budgeted_search_then_regression_is_idempotent_and_tamper_evident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, winner_path = _fixture(tmp_path, monkeypatch)
    winner_path.write_text("# Winner\n", encoding="utf-8")
    winner = SimpleNamespace(
        id=uuid4(), content_sha256=_sha(winner_path.read_bytes()), name="winner"
    )
    job = SimpleNamespace(id=uuid4(), evaluations_used=28)
    search_result = SimpleNamespace(
        job=job,
        candidates=tuple(SimpleNamespace(id=uuid4()) for _ in range(6)),
        winner=winner,
    )
    calls = {"search": 0, "regression": 0}

    def fake_search(_self: object, _spec: object, *, real_authorization: object) -> object:
        calls["search"] += 1
        real_authorization.reserve_completed(40, 40)
        return search_result

    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_runtime.BenchmarkGuidedSkillSearch.run",
        fake_search,
    )
    monkeypatch.setattr(OptimizationStore, "skill_path", lambda _self, _candidate: winner_path)
    dataset_sha = "2" * 64
    evaluation = _evaluation(dataset_sha)
    gate = RegressionGateResult(
        dataset_sha256=dataset_sha,
        base=evaluation,
        winner=evaluation,
        loss_cases=(),
        token_overhead_ratio=0,
        max_loss_cases=0,
        max_token_overhead_ratio=0.25,
        passed=True,
    )

    def fake_regression(*_args: object, **kwargs: object) -> RegressionGateResult:
        calls["regression"] += 1
        authorization = kwargs.get("authorization")
        if authorization is None:
            authorization = _args[-1]
        authorization.reserve_completed(16, 16)
        return gate

    executor = BudgetedRealEvolutionExecutor(tmp_path / "workspace")
    monkeypatch.setattr(executor, "_regression_gate", fake_regression)
    preflight = executor.preflight(spec)
    assert preflight.search_agent_runs == 40
    assert preflight.validation_search_dataset_sha256 == "3" * 64
    assert preflight.regression_dev_dataset_sha256 == "3" * 64
    with pytest.raises(EvolutionRuntimeError, match="below planned Agent Runs"):
        executor.run_search(
            spec,
            RealEvaluationAuthorization(True, max_cost_microusd=40, max_agent_runs=39),
        )
    search = executor.run_search(
        spec,
        RealEvaluationAuthorization(True, max_cost_microusd=40, max_agent_runs=40),
    )
    replay = executor.run_search(
        spec,
        RealEvaluationAuthorization(True, max_cost_microusd=40, max_agent_runs=40),
    )
    assert search.search_receipt is not None
    assert replay.search_receipt == search.search_receipt
    assert calls["search"] == 1
    completed = executor.run_regression(
        spec,
        RealEvaluationAuthorization(True, max_cost_microusd=16, max_agent_runs=16),
    )
    assert completed.regression_receipt is not None
    assert completed.handoff_path is not None
    assert calls == {"search": 1, "regression": 1}
    result_path = completed.directory / "regression-dev-result.json"
    result_path.write_text("{}", encoding="utf-8")
    with pytest.raises(EvolutionRuntimeError, match="artifact mismatch"):
        executor.verify(completed.directory)


def test_execute_cli_requires_explicit_confirmation(tmp_path: Path) -> None:
    config = tmp_path / "runtime.yaml"
    config.write_text("schema_version: invalid\n", encoding="utf-8")
    # The evolution CLI commands were removed during the CLI reduction.
    # The remaining path requires explicit confirmation via the real smoke/run commands.
    result = CliRunner().invoke(
        app,
        ["real", "smoke", str(config)],
        terminal_width=240,
    )
    assert result.exit_code == 2


def test_no_winner_is_persisted_as_a_valid_negative_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _winner_path = _fixture(tmp_path, monkeypatch)
    calls = 0

    def no_winner(
        _self: object, _spec: object, *, real_authorization: RealEvaluationAuthorization
    ) -> object:
        nonlocal calls
        calls += 1
        real_authorization.reserve_completed(40, 40)
        raise SkillSearchError(
            "no search-origin candidate satisfies Pareto and hard constraints"
        )

    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_runtime.BenchmarkGuidedSkillSearch.run",
        no_winner,
    )
    executor = BudgetedRealEvolutionExecutor(tmp_path / "workspace")
    result = executor.run_search(
        spec,
        RealEvaluationAuthorization(True, max_cost_microusd=40, max_agent_runs=40),
    )
    replay = executor.run_search(
        spec,
        RealEvaluationAuthorization(True, max_cost_microusd=40, max_agent_runs=40),
    )
    assert result.search_receipt is not None
    assert result.search_receipt.status == "NO_WINNER"
    assert result.search_receipt.winner_candidate_id is None
    assert replay.search_receipt == result.search_receipt
    assert calls == 1
    with pytest.raises(EvolutionRuntimeError, match="validation_search must complete"):
        executor.run_regression(
            spec,
            RealEvaluationAuthorization(True, max_cost_microusd=16, max_agent_runs=16),
        )
