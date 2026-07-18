from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Iterator

import pytest
import yaml
from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import (
    AutomaticBenchmarkGenerator,
    BenchmarkGenerationSpec,
    DatasetLoader,
)
from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    FailureLabel,
    RealAttemptEvidence,
    RealEvidenceClass,
    RealEvidenceStatus,
    RealRunMode,
    ReviewDecision,
    SearchEvaluationStage,
)
from agentskill_eval_experiment import LocalExperimentStore, ReplayBundleWriter
from agentskill_eval_real_evidence import (
    AgentSpec,
    BaselineReplay,
    PricingSpec,
    ProtocolSpec,
    RealAgentEvidenceRunner,
    RealAgentEvidenceSpec,
    RealEvidenceError,
    RealEvidencePreflight,
    RealEvidenceReportWriter,
    RealPreflightError,
    RunnerSpec,
)
from agentskill_eval_skill_optimizer import (
    CandidateQualityGate,
    RealAgentCandidateEvaluator,
    RealCandidateEvaluationError,
    RealEvaluationAuthorization,
    SearchCase,
)
from agentskill_eval_skill_optimizer.evolution import ImprovementHypothesis
from agentskill_eval_skill_optimizer.optimization_v2 import (
    OptimizationV2Preflight,
    OptimizationV2PreflightResult,
    OptimizationV2ScreeningRunner,
    OptimizationV2Spec,
)

PROJECT = Path(__file__).resolve().parents[2]
FAKE_RUNNER = PROJECT / "tests/fixtures/fake_skill_up.py"
FAKE_AGENT = PROJECT / "tests/fixtures/fake_process_agent.py"
SKILL = PROJECT / "examples/skills/python-bug-fix-v1"
CASE_IDS = (
    "more-itertools-last-reversed-none",
    "more-itertools-sample-strict-counts",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def published_dataset(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("real-evidence-dataset")
    source = root / "source"
    bundle = PROJECT / "examples/benchmark-sources/more-itertools.bundle"
    subprocess.run(
        ("git", "clone", "--quiet", str(bundle), str(source)),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(source),
            "remote",
            "set-url",
            "origin",
            "https://github.com/more-itertools/more-itertools.git",
        ),
        check=True,
    )
    template = BenchmarkGenerationSpec.load(
        PROJECT / "examples/benchmark-sources/more-itertools-generation.example.yaml"
    )
    generator = AutomaticBenchmarkGenerator(root / "generator")
    generated = generator.generate(template.model_copy(update={"repository_path": source}))
    for candidate in generated.candidates:
        generator.review(
            generated.job.id,
            candidate.id,
            "real-evidence-test",
            ReviewDecision.APPROVED,
            "offline evidence checked",
        )
    _version, destination = generator.publish(generated.job.id, "real-evidence-test")
    yield destination


def _spec(
    dataset: Path, *, model: str = "fake-agent", provider: str = "fake-provider"
) -> RealAgentEvidenceSpec:
    return RealAgentEvidenceSpec(
        schema_version="ase/real-agent-evidence/v1alpha1",
        name="fake process real-evidence integration",
        dataset_path=dataset,
        skill_path=SKILL,
        case_ids=CASE_IDS,
        evidence_class=RealEvidenceClass.PROCESS_INTEGRATION,
        simulated=True,
        runner=RunnerSpec(
            path=FAKE_RUNNER,
            expected_sha256=_sha(FAKE_RUNNER),
            expected_version="skill-up version 0.5.0",
        ),
        agent=AgentSpec(
            path=FAKE_AGENT,
            expected_sha256=_sha(FAKE_AGENT),
            expected_version="fake-process-agent 1.0.0",
            engine="fake-process",
            engine_version="1.0.0",
            provider=provider,
            model=model,
            temperature=0,
            seed=7,
            max_turns=4,
            max_tool_calls=8,
            timeout_seconds=5,
            tool_capabilities=("filesystem", "shell", "tests"),
            secret_env_names=("FAKE_PROVIDER_API_KEY", "FAKE_AGENT_COUNTER_FILE"),
            max_input_tokens=500,
            max_output_tokens=100,
        ),
        pricing=PricingSpec(
            input_microusd_per_million_tokens=1,
            output_microusd_per_million_tokens=1,
            estimated_input_tokens_per_run=100,
            estimated_output_tokens_per_run=30,
        ),
        protocol=ProtocolSpec(evidence_repeats=3, bootstrap_resamples=100),
    )


def _set_fake_secrets(monkeypatch: pytest.MonkeyPatch, counter: Path) -> str:
    secret = "never-persist-this-provider-secret"
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", secret)
    monkeypatch.setenv("FAKE_AGENT_COUNTER_FILE", str(counter))
    return secret


def test_spec_rejects_literal_secret_in_agent_home_config(published_dataset: Path) -> None:
    payload = _spec(published_dataset).model_dump(mode="python")
    payload["agent"]["home_config_files"] = {
        ".qwen/settings.json": {"security": {"apiKey": "plaintext-value"}}
    }
    with pytest.raises(ValueError, match="literal Secret field"):
        RealAgentEvidenceSpec.model_validate(payload)


def test_local_agent_can_declare_no_secret_environment(published_dataset: Path) -> None:
    payload = _spec(published_dataset).model_dump(mode="python")
    payload["agent"].update(
        {
            "provider": "qwen-local",
            "engine": "qwen_openai_process",
            "secret_env_names": (),
        }
    )
    spec = RealAgentEvidenceSpec.model_validate(payload)
    assert spec.agent.secret_env_names == ()


def test_qwen_spec_requires_frozen_tool_budget(published_dataset: Path) -> None:
    payload = _spec(published_dataset).model_dump(mode="python")
    payload["agent"].update(
        {
            "engine": "qwen_code",
            "max_tool_calls": 48,
            "home_config_files": {
                ".qwen/settings.json": {"model": {"maxToolCalls": 24}}
            },
        }
    )
    with pytest.raises(ValueError, match="maxToolCalls.*max_tool_calls"):
        RealAgentEvidenceSpec.model_validate(payload)

    payload["agent"]["home_config_files"][".qwen/settings.json"]["model"][
        "maxToolCalls"
    ] = 48
    spec = RealAgentEvidenceSpec.model_validate(payload)
    assert spec.agent.max_tool_calls == 48


def test_pricing_accounts_for_cache_hits() -> None:
    pricing = PricingSpec(
        input_microusd_per_million_tokens=435_000,
        input_cache_hit_microusd_per_million_tokens=3_625,
        output_microusd_per_million_tokens=870_000,
        estimated_input_tokens_per_run=400_000,
        estimated_cache_hit_tokens_per_run=300_000,
        estimated_output_tokens_per_run=5_000,
    )
    assert pricing.estimated_cost_per_run_microusd == 48_938


def test_preflight_rejects_hash_version_and_missing_secret(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _spec(published_dataset)
    monkeypatch.delenv("FAKE_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("FAKE_AGENT_COUNTER_FILE", raising=False)
    with pytest.raises(RealPreflightError, match="Secret"):
        RealEvidencePreflight().check(spec)

    _set_fake_secrets(monkeypatch, tmp_path / "counter")
    bad_hash = spec.model_copy(
        update={"agent": spec.agent.model_copy(update={"expected_sha256": "0" * 64})}
    )
    with pytest.raises(RealPreflightError, match="hash mismatch"):
        RealEvidencePreflight().check(bad_hash)
    bad_version = spec.model_copy(
        update={"runner": spec.runner.model_copy(update={"expected_version": "9.9.9"})}
    )
    with pytest.raises(RealPreflightError, match="version mismatch"):
        RealEvidencePreflight().check(bad_version)


def test_real_cli_preflight_and_authorization_boundary(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_fake_secrets(monkeypatch, tmp_path / "cli-calls.txt")
    integration = _spec(published_dataset)
    integration_path = tmp_path / "integration.yaml"
    integration_path.write_text(
        yaml.safe_dump(integration.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    cli = CliRunner()
    preflight = cli.invoke(app, ["real", "preflight", str(integration_path)])
    assert preflight.exit_code == 0, preflight.stdout
    assert json.loads(preflight.stdout)["simulated"] is True
    refused = cli.invoke(
        app,
        [
            "real",
            "smoke",
            str(integration_path),
            "--confirm-real-run",
            "--max-cost-microusd",
            "1000",
            "--max-agent-runs",
            "4",
        ],
        terminal_width=240,
    )
    assert refused.exit_code == 2
    assert "observed_agent" in refused.output

    observed = integration.model_copy(
        update={"evidence_class": RealEvidenceClass.OBSERVED_AGENT, "simulated": False}
    )
    observed_path = tmp_path / "observed.yaml"
    observed_path.write_text(
        yaml.safe_dump(observed.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    missing_confirmation = cli.invoke(
        app,
        [
            "real",
            "smoke",
            str(observed_path),
            "--max-cost-microusd",
            "1000",
            "--max-agent-runs",
            "4",
        ],
        terminal_width=240,
    )
    assert missing_confirmation.exit_code == 2
    assert not (tmp_path / "cli-calls.txt").exists()


def test_fake_process_smoke_is_auditable_secret_safe_and_idempotent(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "calls.txt"
    secret = _set_fake_secrets(monkeypatch, counter)
    spec = _spec(published_dataset, provider="fake<script>alert(1)</script>")
    workspace = tmp_path / "workspace"
    runner = RealAgentEvidenceRunner(workspace)

    with pytest.raises(RealEvidenceError, match="process-integration"):
        asyncio.run(
            runner.run(
                spec,
                RealRunMode.SMOKE,
                confirm_real_run=True,
                max_cost_microusd=1_000,
                max_agent_runs=4,
            )
        )
    with pytest.raises(RealEvidenceError, match="estimated cost"):
        asyncio.run(
            runner.run(
                spec,
                RealRunMode.SMOKE,
                confirm_real_run=False,
                max_cost_microusd=3,
                max_agent_runs=4,
                allow_process_integration=True,
            )
        )

    result = asyncio.run(
        runner.run(
            spec,
            RealRunMode.SMOKE,
            confirm_real_run=False,
            max_cost_microusd=1_000,
            max_agent_runs=4,
            allow_process_integration=True,
        )
    )
    assert result.manifest.status == RealEvidenceStatus.COMPLETED
    assert result.manifest.simulated is True
    assert result.manifest.real_run_confirmed is False
    assert result.report is not None
    assert result.report.baseline_pass_rate == 0
    assert result.report.treatment_pass_rate == 1
    assert result.report.absolute_gain == 1
    assert counter.read_text(encoding="utf-8") == "4"
    assert result.report_json is not None and result.report_json.is_file()
    assert result.report_html is not None and result.report_html.is_file()
    html = result.report_html.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert result.replay_bundle is not None
    ReplayBundleWriter.verify(result.replay_bundle)
    cli = CliRunner()
    status_result = cli.invoke(
        app,
        ["real", "status", str(workspace), str(result.manifest.experiment_id)],
    )
    assert status_result.exit_code == 0, status_result.stdout
    assert json.loads(status_result.stdout)["status"] == "COMPLETED"
    report_result = cli.invoke(
        app,
        ["real", "report", str(workspace), str(result.manifest.experiment_id)],
    )
    assert report_result.exit_code == 0, report_result.stdout
    assert json.loads(report_result.stdout)["simulated"] is True

    store = LocalExperimentStore(workspace)
    for run in store.list_runs(result.manifest.experiment_id):
        attempt = store.load_selected_attempt(result.manifest.experiment_id, run)
        assert attempt is not None
        activation = store.load_activation_evidence(
            result.manifest.experiment_id, run.id, attempt.attempt_no
        )
        if activation.skill_expected:
            assert activation.installed is True
            assert activation.installed_skill_sha256 == result.report.skill_sha256
        else:
            assert activation.baseline_clean is True
        diagnosis = store.load_failure_diagnosis(
            result.manifest.experiment_id, run.id, attempt.attempt_no
        )
        assert diagnosis is not None

    variants = store.list_variants(result.manifest.experiment_id)
    baseline = next(item for item in variants if item.skill_snapshot is None)
    treatment = next(item for item in variants if item.skill_snapshot is not None)
    assert baseline.agent_snapshot.generation_parameters["max_tool_calls"] == 8
    assert baseline.sandbox_snapshot.resource_limits["max_tool_calls"] == 8
    attempt_path = workspace / "experiments" / str(result.manifest.experiment_id)
    attempt_path /= result.report.attempt_evidence_paths[0]
    attempt_payload = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt_payload["simulated"] = False
    with pytest.raises(ValueError, match="real and simulated"):
        RealEvidenceReportWriter._assert_uniform_evidence(
            result.manifest,
            baseline,
            treatment,
            (RealAttemptEvidence.model_construct(**attempt_payload),),
        )

    persisted = b"".join(path.read_bytes() for path in workspace.rglob("*") if path.is_file())
    assert secret.encode() not in persisted
    assert str(counter).encode() not in persisted

    replayed = asyncio.run(
        runner.run(
            spec,
            RealRunMode.SMOKE,
            confirm_real_run=False,
            max_cost_microusd=1_000,
            max_agent_runs=4,
            allow_process_integration=True,
        )
    )
    assert replayed.manifest.experiment_id == result.manifest.experiment_id
    assert counter.read_text(encoding="utf-8") == "4"


def test_budget_exhaustion_stops_new_agent_runs(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "budget-calls.txt"
    _set_fake_secrets(monkeypatch, counter)
    spec = _spec(published_dataset)
    result = asyncio.run(
        RealAgentEvidenceRunner(tmp_path / "budget-workspace").run(
            spec,
            RealRunMode.SMOKE,
            confirm_real_run=False,
            max_cost_microusd=300,
            max_agent_runs=4,
            allow_process_integration=True,
        )
    )
    assert result.manifest.status == RealEvidenceStatus.BUDGET_EXHAUSTED
    assert int(counter.read_text(encoding="utf-8")) < 4
    assert result.report is None


def test_invalid_process_results_never_become_success_evidence(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_fake_secrets(monkeypatch, tmp_path / "invalid-calls.txt")
    result = asyncio.run(
        RealAgentEvidenceRunner(tmp_path / "invalid-workspace").run(
            _spec(published_dataset, model="fake-invalid"),
            RealRunMode.SMOKE,
            confirm_real_run=False,
            max_cost_microusd=1_000,
            max_agent_runs=4,
            allow_process_integration=True,
        )
    )
    assert result.manifest.status == RealEvidenceStatus.COMPLETED
    assert result.manifest.invalid_runs == 4
    assert result.report is not None and result.report.invalid_runs == 4


def test_real_candidate_evaluator_reuses_observed_runtime_and_case_cache(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "optimizer-calls.txt"
    _set_fake_secrets(monkeypatch, counter)
    observed = _spec(published_dataset).model_copy(
        update={"evidence_class": RealEvidenceClass.OBSERVED_AGENT, "simulated": False}
    )
    config = tmp_path / "real-optimizer.yaml"
    config.write_text(
        yaml.safe_dump(observed.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    authorization = RealEvaluationAuthorization(
        confirm_real_run=True,
        max_cost_microusd=1_000,
        max_agent_runs=4,
    )
    evaluator = RealAgentCandidateEvaluator(config, tmp_path / "workspace", authorization)
    cases = tuple(SearchCase(id=case_id) for case_id in CASE_IDS)
    candidate = SKILL / "SKILL.md"

    search_config = tmp_path / "real-search.yaml"
    search_config.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/optimization-search/v1alpha1",
                "name": "real optimizer gate test",
                "base_skill_path": str(SKILL),
                "manual_skill_path": str(SKILL),
                "validation_search_path": str(published_dataset),
                "mutations": [
                    {
                        "id": f"candidate-{index}",
                        "hypothesis": f"Candidate hypothesis number {index} is explicit.",
                        "instruction": f"Apply candidate instruction number {index} carefully.",
                    }
                    for index in range(1, 4)
                ],
                "search": {
                    "algorithm": "successive_halving",
                    "subset_size": 2,
                    "promote_search_candidates": 1,
                    "random_seed": 7,
                },
                "budget": {"max_candidate_case_evaluations": 20, "timeout_seconds": 30},
                "evaluator": {
                    "type": "real_agent",
                    "real_agent_config_path": str(config),
                    "version": "test-v1",
                    "simulated": False,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    refused_cli = CliRunner().invoke(
        app,
        ["optimize", "search", str(search_config), "--workspace", str(tmp_path / "cli")],
    )
    assert refused_cli.exit_code == 2
    assert "real_agent evaluator requires" in refused_cli.output
    assert not counter.exists()

    refused = RealAgentCandidateEvaluator(
        config,
        tmp_path / "refused-workspace",
        RealEvaluationAuthorization(
            confirm_real_run=True,
            max_cost_microusd=1_000,
            max_agent_runs=2,
        ),
    )
    with pytest.raises(RealCandidateEvaluationError, match="requires 4 Agent Runs"):
        refused.authorize_plan(2)
    assert not counter.exists()

    first = evaluator.evaluate(
        candidate,
        published_dataset / "dataset.yaml",
        DatasetLoader().load(published_dataset).dataset_sha256,
        cases,
        SearchEvaluationStage.SUBSET,
        30,
    )
    replay = evaluator.evaluate(
        candidate,
        published_dataset / "dataset.yaml",
        DatasetLoader().load(published_dataset).dataset_sha256,
        cases,
        SearchEvaluationStage.FULL,
        30,
    )

    assert first.simulated is False
    assert all(item.outcome == "pass" for item in first.results)
    assert all(item.trace_ref and Path(item.trace_ref).is_file() for item in first.results)
    assert all(item.experiment_id and item.run_id and item.attempt_id for item in first.results)
    assert replay.results == first.results
    assert counter.read_text(encoding="utf-8") == "4"
    assert authorization.consumed_agent_runs == 4

    search_run = CliRunner().invoke(
        app,
        [
            "optimize",
            "search",
            str(search_config),
            "--workspace",
            str(tmp_path / "real-search-workspace"),
            "--confirm-real-run",
            "--max-cost-microusd",
            "10000",
            "--max-agent-runs",
            "24",
        ],
        terminal_width=240,
    )
    assert search_run.exit_code == 1
    assert isinstance(search_run.exception, Exception)
    assert "no search-origin candidate" in str(search_run.exception)
    # Search now reuses the frozen v1 baseline across candidate pairs:
    # 4 runs for the first candidate and 2 treatment runs for each later pair.
    assert counter.read_text(encoding="utf-8") == "16"


def test_real_candidate_evaluator_reuses_v1_baseline_across_candidates(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "baseline-reuse-calls.txt"
    _set_fake_secrets(monkeypatch, counter)
    observed = _spec(published_dataset).model_copy(
        update={"evidence_class": RealEvidenceClass.OBSERVED_AGENT, "simulated": False}
    )
    baseline_cache: dict[str, BaselineReplay] = {}
    authorization = RealEvaluationAuthorization(
        confirm_real_run=True,
        max_cost_microusd=10_000,
        max_agent_runs=8,
    )
    candidate_one = tmp_path / "candidate-one"
    candidate_two = tmp_path / "candidate-two"
    candidate_three = tmp_path / "candidate-three"
    candidate_one.mkdir()
    candidate_two.mkdir()
    candidate_three.mkdir()
    candidate_one.joinpath("SKILL.md").write_bytes(
        SKILL.joinpath("SKILL.md").read_bytes() + b"\n- Verify once.\n"
    )
    candidate_two.joinpath("SKILL.md").write_bytes(
        SKILL.joinpath("SKILL.md").read_bytes() + b"\n- Retry once.\n"
    )
    candidate_three.joinpath("SKILL.md").write_bytes(
        SKILL.joinpath("SKILL.md").read_bytes() + b"\n- Re-check twice.\n"
    )

    def write_config(path: Path, skill: Path) -> None:
        path.write_text(
            yaml.safe_dump(
                observed.model_copy(update={"skill_path": skill}).model_dump(mode="json"),
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    cases = tuple(SearchCase(id=case_id) for case_id in CASE_IDS)
    first_config = tmp_path / "candidate-one.yaml"
    write_config(first_config, candidate_one)
    first = RealAgentCandidateEvaluator(
        first_config,
        tmp_path / "reuse-workspace",
        authorization,
        baseline_skill_path=SKILL,
        baseline_replay_cache=baseline_cache,
    ).evaluate(
        candidate_one / "SKILL.md",
        published_dataset / "dataset.yaml",
        DatasetLoader().load(published_dataset).dataset_sha256,
        cases,
        SearchEvaluationStage.FULL,
        30,
    )
    second_config = tmp_path / "candidate-two.yaml"
    write_config(second_config, candidate_two)
    second = RealAgentCandidateEvaluator(
        second_config,
        tmp_path / "reuse-workspace",
        authorization,
        baseline_skill_path=SKILL,
        baseline_replay_cache=baseline_cache,
    ).evaluate(
        candidate_two / "SKILL.md",
        published_dataset / "dataset.yaml",
        DatasetLoader().load(published_dataset).dataset_sha256,
        cases,
        SearchEvaluationStage.FULL,
        30,
    )

    assert all(item.outcome == "pass" for item in first.results + second.results)
    assert len(baseline_cache) == len(CASE_IDS)
    assert all(len(key) == 64 for key in baseline_cache)
    third_config = tmp_path / "candidate-three.yaml"
    write_config(third_config, candidate_three)
    third = RealAgentCandidateEvaluator(
        third_config,
        tmp_path / "reuse-workspace",
        authorization,
        baseline_skill_path=SKILL,
        baseline_replay_cache=baseline_cache,
    ).evaluate(
        candidate_three / "SKILL.md",
        published_dataset / "dataset.yaml",
        DatasetLoader().load(published_dataset).dataset_sha256,
        cases,
        SearchEvaluationStage.FULL,
        30,
    )

    assert all(item.outcome == "pass" for item in third.results)
    # 4 first-pair Runs + 2 treatment Runs per later candidate.
    assert counter.read_text(encoding="utf-8") == "8"
    assert authorization.consumed_agent_runs == 8


def _resume_test_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    published_dataset: Path,
    *,
    model: str = "fake-agent",
) -> tuple[OptimizationV2Spec, OptimizationV2ScreeningRunner, Path, Path]:
    workspace = tmp_path / "optimization-v2-workspace"
    quality_root = workspace / "preflight" / "candidate-quality"
    proposal = tmp_path / "proposal"
    proposal.mkdir()
    proposal_manifest = proposal / "proposal-manifest.json"
    proposal_manifest.write_text('{"fixture":"resume"}\n', encoding="utf-8")
    failure_bundle = tmp_path / "failure-bundle.yaml"
    failure_bundle.write_text("fixture: resume\n", encoding="utf-8")
    observed = _spec(published_dataset, model=model).model_copy(
        update={"evidence_class": RealEvidenceClass.OBSERVED_AGENT, "simulated": False}
    )
    config_path = tmp_path / "observed-agent.yaml"
    config_path.write_text(
        yaml.safe_dump(observed.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    hypotheses = tuple(
        ImprovementHypothesis(
            id=candidate_id,
            failure_label=FailureLabel.VERIFICATION,
            hypothesis="Verification discipline reduces unsupported completion claims.",
            instruction=instruction,
            evidence_refs=("train-evidence-ref",),
        )
        for candidate_id, instruction in (
            (
                "candidate-alpha",
                "Inspect the result and verify the targeted outcome before closing.",
            ),
            ("candidate-bravo", "Validate the observed result before making a completion claim."),
            ("candidate-charlie", "Check the executed outcome and retry only with new evidence."),
        )
    )
    CandidateQualityGate(quality_root).materialize_hypotheses(
        proposal_job_id="resume-fixture",
        proposal_manifest_sha256=_sha(proposal_manifest),
        parent_content=(SKILL / "SKILL.md").read_bytes(),
        hypotheses=hypotheses,
        max_candidates=3,
    )
    spec = OptimizationV2Spec(
        schema_version="ase/optimization-evaluation-v2/v1alpha1",
        name="fake-process-resume",
        base_skill_path=SKILL,
        proposal_directory=proposal,
        failure_bundle_path=failure_bundle,
        real_agent_config_path=config_path,
        validation_search_path=published_dataset,
        case_ids=CASE_IDS,
        target_provider="fake-provider",
        target_model=model,
        max_candidates=3,
        max_agent_runs=12,
    )
    dataset_sha = DatasetLoader().load(published_dataset).dataset_sha256
    parent_sha = _sha(SKILL / "SKILL.md")

    def prepared(
        _self: OptimizationV2ScreeningRunner, current: OptimizationV2Spec
    ) -> tuple[OptimizationV2PreflightResult, object, RealAgentEvidenceSpec]:
        verified = CandidateQualityGate(quality_root).verify(
            quality_root / "candidate-quality-report.json"
        )
        agent = RealAgentEvidenceSpec.load(current.real_agent_config_path)
        report = OptimizationV2Preflight(
            name=current.name,
            status="READY",
            proposal_job_id="resume-fixture",
            proposal_manifest_sha256=_sha(proposal_manifest),
            parent_skill_sha256=parent_sha,
            candidate_quality_report_sha256=_sha(
                quality_root / "candidate-quality-report.json"
            ),
            accepted_candidate_ids=verified.accepted_candidate_ids,
            rejected_candidate_ids=verified.rejected_candidate_ids,
            dataset_sha256=dataset_sha,
            case_ids=current.case_ids,
            provider=agent.agent.provider,
            model=agent.agent.model,
            planned_agent_runs=12,
            expected_new_agent_runs=8,
            estimated_cost_microusd=3_000,
            estimated_new_cost_microusd=2_000,
        )
        return OptimizationV2PreflightResult(
            report=report,
            candidate_quality=verified,
            report_path=quality_root / "fixture-preflight.json",
            html_path=quality_root / "fixture-preflight.html",
        ), verified, agent

    monkeypatch.setattr(OptimizationV2ScreeningRunner, "_prepared_inputs", prepared)
    return spec, OptimizationV2ScreeningRunner(workspace), config_path, proposal_manifest


def test_optimization_v2_resume_reuses_completed_work_and_exposes_cli_status(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "resume-calls.txt"
    secret = _set_fake_secrets(monkeypatch, counter)
    spec, runner, _config_path, _proposal_manifest = _resume_test_context(
        tmp_path, monkeypatch, published_dataset, model="fake-invalid-after-four"
    )
    partial, report_path, html_path = runner.run(
        spec,
        confirm_real_run=True,
        max_cost_microusd=10_000,
        max_agent_runs=8,
    )

    assert partial.status == "PARTIAL"
    assert partial.completed_candidate_ids == ("candidate-alpha",)
    assert partial.invalid_candidate_ids == ("candidate-bravo",)
    assert partial.remaining_candidate_ids == ("candidate-charlie",)
    assert partial.error_counts["agent_invalid"] >= 1
    assert counter.read_text(encoding="utf-8") == "6"
    assert report_path.is_file() and html_path.is_file()
    assert "Remaining candidates" in html_path.read_text(encoding="utf-8")

    resumed, _report_path, _html_path = runner.resume(
        spec,
        confirm_real_run=True,
        max_cost_microusd=10_000,
        max_agent_runs=2,
    )
    assert resumed.status == "PARTIAL"
    assert resumed.completed_candidate_ids == ("candidate-alpha", "candidate-charlie")
    assert resumed.invalid_candidate_ids == ("candidate-bravo",)
    assert resumed.remaining_candidate_ids == ()
    assert resumed.baseline_reused_runs == 4
    assert counter.read_text(encoding="utf-8") == "8"

    replay, _report_path, _html_path = runner.resume(
        spec,
        confirm_real_run=True,
        max_cost_microusd=1,
        max_agent_runs=1,
    )
    assert replay.remaining_candidate_ids == ()
    assert counter.read_text(encoding="utf-8") == "8"

    cli = CliRunner()
    status = cli.invoke(app, ["optimize", "v2", "status", str(runner.workspace)])
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)["remaining_candidate_ids"] == []
    spec_path = tmp_path / "optimization-v2.yaml"
    spec_path.write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    resumed_cli = cli.invoke(
        app,
        [
            "optimize",
            "v2",
            "resume",
            str(spec_path),
            "--workspace",
            str(runner.workspace),
            "--confirm-real-run",
            "--max-agent-runs",
            "1",
            "--max-cost-microusd",
            "1",
        ],
    )
    assert resumed_cli.exit_code == 0, resumed_cli.output
    assert counter.read_text(encoding="utf-8") == "8"

    persisted = b"".join(
        path.read_bytes() for path in runner.workspace.rglob("*") if path.is_file()
    )
    assert secret.encode() not in persisted
    assert str(counter).encode() not in persisted


def test_optimization_v2_classifies_deepseek_402_without_skill_win(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "balance-calls.txt"
    _set_fake_secrets(monkeypatch, counter)
    spec, runner, _config_path, _proposal_manifest = _resume_test_context(
        tmp_path, monkeypatch, published_dataset, model="fake-402"
    )
    report, _report_path, _html_path = runner.run(
        spec,
        confirm_real_run=True,
        max_cost_microusd=10_000,
        max_agent_runs=4,
    )

    candidate = report.candidates[0]
    assert report.status == "BLOCKED"
    assert report.provider_blocked_candidate_ids == ("candidate-alpha",)
    assert report.error_counts["insufficient_balance"] >= 1
    assert candidate.error_types == ("insufficient_balance",)
    assert candidate.wtl == {"win": 0, "tie": 0, "loss": 0}
    assert report.observed_cost_microusd == 0
    assert counter.read_text(encoding="utf-8") == "4"


def test_optimization_v2_resume_rejects_drift_and_tampered_inputs(
    published_dataset: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counter = tmp_path / "drift-calls.txt"
    _set_fake_secrets(monkeypatch, counter)
    spec, runner, config_path, proposal_manifest = _resume_test_context(
        tmp_path, monkeypatch, published_dataset, model="fake-invalid-after-four"
    )
    runner.run(
        spec,
        confirm_real_run=True,
        max_cost_microusd=10_000,
        max_agent_runs=8,
    )
    calls_before = counter.read_text(encoding="utf-8")

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["name"] = "drifted-agent-config"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="resume rejected"):
        runner.resume(
            spec,
            confirm_real_run=True,
            max_cost_microusd=10_000,
            max_agent_runs=2,
        )
    assert counter.read_text(encoding="utf-8") == calls_before

    payload["name"] = "fake process real-evidence integration"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    proposal_manifest.write_text('{"fixture":"tampered"}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="resume rejected"):
        runner.resume(
            spec,
            confirm_real_run=True,
            max_cost_microusd=10_000,
            max_agent_runs=2,
        )
    assert counter.read_text(encoding="utf-8") == calls_before

    proposal_manifest.write_text('{"fixture":"resume"}\n', encoding="utf-8")
    candidate_skill = (
        runner.workspace
        / "preflight"
        / "candidate-quality"
        / "candidate-skills"
        / "candidate-charlie"
        / "SKILL.md"
    )
    candidate_skill.write_text(
        candidate_skill.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="candidate hash mismatch"):
        runner.resume(
            spec,
            confirm_real_run=True,
            max_cost_microusd=10_000,
            max_agent_runs=2,
        )
    assert counter.read_text(encoding="utf-8") == calls_before
