"""Stage 3B Process rehearsal tests; no model or Agent is invoked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import (
    DatasetSplit,
    OptimizationBenchmarkPublisher,
    OptimizationBenchmarkRelease,
    SplitDatasetReference,
)
from agentskill_eval_cli.main import app
from agentskill_eval_contracts import stable_sha256
from agentskill_eval_skill_optimizer import (
    DatasetPlanDescriptor,
    DryRunProcessSpec,
    EvolutionDryRunError,
    EvolutionDryRunOrchestrator,
    EvolutionDryRunSpec,
)
from agentskill_eval_skill_optimizer.execution_dry_run import DatasetLoader
from agentskill_eval_skill_optimizer.execution_plan import RealEvolutionExecutionPlanner

runner = CliRunner()


def _reference(split: DatasetSplit, index: int) -> SplitDatasetReference:
    return SplitDatasetReference(
        split=split,
        dataset_version_id=UUID(int=index),
        dataset_content_sha256=f"{index:064x}",
        relative_path=f"dataset-versions/{index}",
        case_count=4,
        candidate_keys=tuple(f"{split.value}-case-{item}" for item in range(4)),
        source_lineages=(f"github.com/example/repository-{index}",),
        independence_groups=tuple(f"family-{index}-{item}" for item in range(4)),
        command_evidence_count=48,
    )


def _release() -> OptimizationBenchmarkRelease:
    references = tuple(
        _reference(split, index)
        for index, split in enumerate(
            (
                DatasetSplit.TRAIN,
                DatasetSplit.VALIDATION_SEARCH,
                DatasetSplit.REGRESSION_DEV,
                DatasetSplit.VALIDATION_CONFIRM,
                DatasetSplit.LOCKED_TEST,
            ),
            start=1,
        )
    )
    payload = {
        "schema_version": "ase/optimization-benchmark-release/v1",
        "name": "stage-3b-fixture",
        "version": "1",
        "plan_sha256": "a" * 64,
        "generator_version": "fixture",
        "verifier_version": "fixture",
        "total_case_count": 20,
        "repository_count": 5,
        "independence_group_count": 20,
        "splits": [item.model_dump(mode="json") for item in references],
        "locked_policy": "withheld_until_one_shot_final_evaluation",
        "claim_limit": "fixture publication only",
    }
    return OptimizationBenchmarkRelease.model_validate(
        {**payload, "content_sha256": stable_sha256(payload)}
    )


def _process(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "process-stages.log"
    script = tmp_path / "fake-dry-run-process.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('fake-evolution-dry-run 1.0')\n"
        "    raise SystemExit(0)\n"
        "request = json.load(sys.stdin)\n"
        "log = os.environ.get('ASE_DRY_RUN_LOG')\n"
        "if log:\n"
        "    with open(log, 'a', encoding='utf-8') as stream:\n"
        "        stream.write(request['stage'] + '\\n')\n"
        "print(json.dumps({\n"
        "    'schema_version': 'ase/evolution-dry-run-process-response/v1alpha1',\n"
        "    'dry_run_id': request['dry_run_id'],\n"
        "    'stage': request['stage'],\n"
        "    'dataset_version_sha256': request['dataset_version_sha256'],\n"
        "    'accepted': True,\n"
        "}, sort_keys=True))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    release = _release()
    benchmark = tmp_path / "benchmark"
    release_dir = benchmark / "optimization-benchmark-releases" / "fixture" / "1"
    release_dir.mkdir(parents=True)
    release_path = release_dir / "release-manifest.json"
    release_path.write_text(release.model_dump_json(indent=2), encoding="utf-8")
    optimizer_view = release_dir / "optimizer-view.json"
    optimizer_view.write_text(
        json.dumps(OptimizationBenchmarkPublisher.optimizer_view(release), indent=2),
        encoding="utf-8",
    )
    for reference in release.splits:
        (benchmark / reference.relative_path).mkdir(parents=True)

    plan_dir = tmp_path / "plan-workspace" / "evolution-execution-plans" / str(uuid4())
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "execution-plan.json"
    manifest_path = plan_dir / "execution-plan-manifest.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    manifest_path.write_text("{}\n", encoding="utf-8")
    descriptors = {
        item.split.value: DatasetPlanDescriptor(
            split=item.split.value,
            dataset_version_sha256=item.dataset_content_sha256,
            split_plan_sha256=release.plan_sha256,
            case_count=item.case_count,
            independent_group_count=len(item.independence_groups),
        )
        for item in release.splits
        if item.split != DatasetSplit.TRAIN
    }
    plan = SimpleNamespace(
        plan_id=uuid4(),
        datasets=SimpleNamespace(**descriptors),
    )
    plan_result = SimpleNamespace(
        plan=plan,
        plan_path=plan_path,
        manifest_path=manifest_path,
    )
    monkeypatch.setattr(RealEvolutionExecutionPlanner, "verify", lambda _self, _path: plan_result)

    def fake_load(_self: object, root: Path) -> SimpleNamespace:
        index = int(root.name)
        reference = release.splits[index - 1]
        return SimpleNamespace(
            dataset_version=SimpleNamespace(
                id=reference.dataset_version_id,
                content_sha256=reference.dataset_content_sha256,
            ),
            cases=tuple(range(reference.case_count)),
            independence_groups=reference.independence_groups,
        )

    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_dry_run.DatasetLoader.load", fake_load
    )
    monkeypatch.setattr(
        "agentskill_eval_skill_optimizer.execution_dry_run."
        "BenchmarkStore.assert_dataset_version_integrity",
        lambda _version, _root: None,
    )

    process, log = _process(tmp_path)
    monkeypatch.setenv("ASE_DRY_RUN_LOG", str(log))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-inherited-or-written")
    payload = {
        "schema_version": "ase/evolution-dry-run-spec/v1alpha1",
        "name": "stage-3b-process-rehearsal",
        "execution_plan_directory": str(plan_dir),
        "benchmark_workspace": str(benchmark),
        "release_manifest": str(release_path),
        "optimizer_view": str(optimizer_view),
        "process": {
            "executable": str(process),
            "expected_sha256": hashlib.sha256(process.read_bytes()).hexdigest(),
            "expected_version_output": "fake-evolution-dry-run 1.0",
            "allowed_environment": ["PATH", "ASE_DRY_RUN_LOG"],
        },
        "claim_limit": (
            "Process integration evidence only; no model, Agent, holdout access, or Skill "
            "improvement claim."
        ),
    }
    config = tmp_path / "dry-run.yaml"
    config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return config


def test_prepare_is_idempotent_and_never_rehearses_protected_splits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _inputs(tmp_path, monkeypatch)
    spec = EvolutionDryRunSpec.load(config)
    orchestrator = EvolutionDryRunOrchestrator(tmp_path / "output")
    loaded_roots: list[str] = []
    original_load = DatasetLoader.load

    def recording_load(instance: object, root: Path) -> SimpleNamespace:
        loaded_roots.append(root.name)
        return original_load(instance, root)

    monkeypatch.setattr(DatasetLoader, "load", recording_load)

    first = orchestrator.prepare(spec)
    second = orchestrator.prepare(spec)

    assert first.directory == second.directory
    assert first.report == second.report
    assert [item.split for item in first.report.adaptive_bindings] == [
        "validation_search",
        "regression_dev",
    ]
    assert [item.split for item in first.report.withheld_receipts] == [
        "validation_confirm",
        "locked_test",
    ]
    assert [item.stage for item in first.report.process_evidence] == [
        "validation_search",
        "regression_dev",
    ]
    assert (tmp_path / "process-stages.log").read_text(encoding="utf-8").splitlines() == [
        "validation_search",
        "regression_dev",
    ]
    assert set(loaded_roots) == {"2", "3"}
    assert "4" not in loaded_roots
    assert "5" not in loaded_roots
    stored = "\n".join(
        path.read_text(encoding="utf-8")
        for path in first.directory.iterdir()
        if path.is_file()
    )
    assert "DEEPSEEK_API_KEY" not in stored
    assert "must-not-be-inherited-or-written" not in stored
    receipts = (first.directory / "withheld-receipts.json").read_text(encoding="utf-8")
    assert "relative_path" not in receipts
    assert "candidate_keys" not in receipts


def test_rejects_mismatched_plan_leaking_view_and_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _inputs(tmp_path, monkeypatch)
    spec = EvolutionDryRunSpec.load(config)
    view = json.loads(spec.optimizer_view.read_text(encoding="utf-8"))
    view["withheld_splits"][0]["relative_path"] = "secret/path"
    spec.optimizer_view.write_text(json.dumps(view), encoding="utf-8")
    with pytest.raises(EvolutionDryRunError, match="does not exactly match"):
        EvolutionDryRunOrchestrator(tmp_path).preflight(spec)

    with pytest.raises(ValidationError, match="allowlist"):
        DryRunProcessSpec(
            executable=spec.process.executable,
            expected_sha256=spec.process.expected_sha256,
            expected_version_output=spec.process.expected_version_output,
            allowed_environment=("OPENAI_API_KEY",),
        )


def test_rejects_plan_dataset_and_process_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _inputs(tmp_path, monkeypatch)
    spec = EvolutionDryRunSpec.load(config)
    plan_result = RealEvolutionExecutionPlanner(tmp_path).verify(spec.execution_plan_directory)
    bad_search = plan_result.plan.datasets.validation_search.model_copy(
        update={"dataset_version_sha256": "f" * 64}
    )
    bad_plan = SimpleNamespace(
        plan_id=plan_result.plan.plan_id,
        datasets=SimpleNamespace(
            validation_search=bad_search,
            regression_dev=plan_result.plan.datasets.regression_dev,
            validation_confirm=plan_result.plan.datasets.validation_confirm,
            locked_test=plan_result.plan.datasets.locked_test,
        ),
    )
    monkeypatch.setattr(
        RealEvolutionExecutionPlanner,
        "verify",
        lambda _self, _path: SimpleNamespace(
            plan=bad_plan,
            plan_path=plan_result.plan_path,
            manifest_path=plan_result.manifest_path,
        ),
    )
    with pytest.raises(EvolutionDryRunError, match="DatasetVersion mismatch"):
        EvolutionDryRunOrchestrator(tmp_path).preflight(spec)

    monkeypatch.setattr(
        RealEvolutionExecutionPlanner,
        "verify",
        lambda _self, _path: plan_result,
    )
    bad_process = spec.process.model_copy(update={"expected_sha256": "0" * 64})
    with pytest.raises(EvolutionDryRunError, match="executable SHA-256 mismatch"):
        EvolutionDryRunOrchestrator(tmp_path).preflight(
            spec.model_copy(update={"process": bad_process})
        )


def test_cli_prepare_inspect_verify_and_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _inputs(tmp_path, monkeypatch)
    workspace = tmp_path / "cli-workspace"

    preflight = runner.invoke(app, ["evolution", "dry-run", "preflight", str(config)])
    assert preflight.exit_code == 0, preflight.output
    assert json.loads(preflight.stdout)["locked_content_accessed"] is False

    prepared = runner.invoke(
        app,
        [
            "evolution",
            "dry-run",
            "prepare",
            str(config),
            "--workspace",
            str(workspace),
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    summary = json.loads(prepared.stdout)
    directory = Path(summary["directory"])
    assert summary["status"] == "AWAITING_REAL_AUTHORIZATION"

    inspected = runner.invoke(app, ["evolution", "dry-run", "inspect", str(directory)])
    assert inspected.exit_code == 0, inspected.output
    verified = runner.invoke(app, ["evolution", "dry-run", "verify", str(directory)])
    assert verified.exit_code == 0, verified.output

    report = directory / "dry-run-report.md"
    report.write_text(report.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    tampered = runner.invoke(app, ["evolution", "dry-run", "verify", str(directory)])
    assert tampered.exit_code != 0
    assert "artifact mismatch" in tampered.output
