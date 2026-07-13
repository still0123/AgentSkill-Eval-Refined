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

from agentskill_eval_benchmark_gen import AutomaticBenchmarkGenerator, BenchmarkGenerationSpec
from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    RealAttemptEvidence,
    RealEvidenceClass,
    RealEvidenceStatus,
    RealRunMode,
    ReviewDecision,
)
from agentskill_eval_experiment import LocalExperimentStore, ReplayBundleWriter
from agentskill_eval_real_evidence import (
    AgentSpec,
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
    )
    assert refused.exit_code == 2
    assert "observed_agent" in refused.stdout

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
    )
    assert missing_confirmation.exit_code == 2
    assert "confirm-real-run" in missing_confirmation.stdout


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
