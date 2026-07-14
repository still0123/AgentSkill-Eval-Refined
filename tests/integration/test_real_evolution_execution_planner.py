"""Planning-only Stage 3A integration tests; no model or Agent is invoked."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_skill_optimizer import (
    EvolutionExecutionPlanError,
    EvolutionExecutionPlanSpec,
    RealEvolutionExecutionPlanner,
)
from agentskill_eval_skill_optimizer.deepseek_generator import (
    DeepSeekGeneratorInvocationEvidence,
)
from agentskill_eval_skill_optimizer.proposal import (
    ProposalGeneratorParameters,
    RealLLMProposalManifest,
    RealLLMProposalResult,
    RealLLMProposalService,
)

ROOT = Path(__file__).resolve().parents[2]
REAL_AGENT_CONFIG = ROOT / "examples/real-agent-evidence/deepseek-qwen.example.yaml"
runner = CliRunner()


def _fake_proposal(tmp_path: Path) -> RealLLMProposalResult:
    digest = "a" * 64
    evidence = DeepSeekGeneratorInvocationEvidence(
        model="deepseek-v4-pro",
        generator_version="test-generator-v1",
        prompt_sha256=digest,
        output_schema_sha256=digest,
        request_sha256=digest,
        response_sha256=digest,
        hypotheses_sha256=digest,
        hypothesis_count=4,
        input_tokens=100,
        cached_input_tokens=0,
        output_tokens=100,
        reasoning_tokens=0,
        cost_microusd=100,
        duration_ms=1,
    )
    manifest = RealLLMProposalManifest(
        proposal_job_id=uuid4(),
        name="fake audited proposal",
        model="deepseek-v4-pro",
        generator_version="test-generator-v1",
        generator_identity="fake-generator",
        prompt_sha256=digest,
        output_schema_sha256=digest,
        request_sha256=digest,
        base_skill_sha256="b" * 64,
        failure_bundle_sha256="c" * 64,
        proposals_sha256=digest,
        proposal_count=4,
        input_evidence_class="simulated_fixture",
        parameters=ProposalGeneratorParameters(
            base_url="https://api.deepseek.com",
            temperature=0,
            max_output_tokens=1000,
            timeout_seconds=60,
            input_cache_miss_microusd_per_million=1,
            input_cache_hit_microusd_per_million=1,
            output_microusd_per_million=1,
        ),
        invocation_evidence=evidence,
        artifacts={},
        claim_limit="Fixture proposal; no improvement claim.",
    )
    directory = tmp_path / "proposal-jobs" / str(manifest.proposal_job_id)
    directory.mkdir(parents=True)
    (directory / "proposal-manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return RealLLMProposalResult(
        manifest=manifest,
        directory=directory,
        proposals_path=directory / "proposals.json",
        report_json=directory / "proposal-report.json",
        report_html=directory / "proposal-report.html",
    )


def _config(tmp_path: Path, proposal: RealLLMProposalResult) -> Path:
    hashes = {
        "validation_search": "1" * 64,
        "regression_dev": "2" * 64,
        "validation_confirm": "3" * 64,
        "locked_test": "4" * 64,
    }
    counts = {
        "validation_search": 5,
        "regression_dev": 3,
        "validation_confirm": 2,
        "locked_test": 2,
    }
    payload = {
        "schema_version": "ase/real-evolution-execution-plan-spec/v1alpha1",
        "name": "stage-3a-fake-plan",
        "proposal_directory": str(proposal.directory),
        "real_agent_config_path": str(REAL_AGENT_CONFIG),
        "datasets": {
            split: {
                "split": split,
                "dataset_version_sha256": digest,
                "split_plan_sha256": "f" * 64,
                "case_count": counts[split],
                "independent_group_count": counts[split],
                "content_access": "metadata_only",
            }
            for split, digest in hashes.items()
        },
        "search": {
            "subset_size": 2,
            "promote_search_candidates": 2,
            "random_seed": 2026,
        },
        "final": {"validation_confirm_repeats": 3, "locked_test_repeats": 1},
        "budgets": {
            split: {"max_agent_runs": 100, "max_cost_microusd": 10_000_000}
            for split in hashes
        },
        "claim_limit": "Planning evidence only; no Agent was run and no Skill gain is claimed.",
    }
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def proposal_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> RealLLMProposalResult:
    result = _fake_proposal(tmp_path)
    monkeypatch.setattr(RealLLMProposalService, "verify", lambda _self, _path: result)
    return result


def test_plan_math_is_exact_metadata_only_and_idempotent(
    tmp_path: Path, proposal_stub: RealLLMProposalResult
) -> None:
    config = _config(tmp_path, proposal_stub)
    spec = EvolutionExecutionPlanSpec.load(config)
    planner = RealEvolutionExecutionPlanner(tmp_path / "workspace")
    plan = planner.preflight(spec)

    assert [stage.agent_runs for stage in plan.stages] == [58, 12, 24, 8]
    assert plan.total_agent_runs == 102
    assert plan.total_estimated_cost_microusd == sum(
        stage.estimated_cost_microusd for stage in plan.stages
    )
    assert plan.real_calls_executed is False
    assert plan.locked_content_accessed is False
    assert plan.datasets.locked_test.content_access == "metadata_only"
    assert "path" not in plan.datasets.locked_test.model_dump(mode="json")
    assert plan.stages[-1].locked_receipt_required is True

    first = planner.prepare(spec)
    second = planner.prepare(spec)
    assert first.plan == second.plan
    assert first.directory == second.directory
    assert planner.verify(first.directory).plan == plan


def test_plan_rejects_under_budget_and_split_mismatch(
    tmp_path: Path, proposal_stub: RealLLMProposalResult
) -> None:
    config = _config(tmp_path, proposal_stub)
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload["budgets"]["validation_search"]["max_agent_runs"] = 57
    config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(EvolutionExecutionPlanError, match="below the required envelope"):
        RealEvolutionExecutionPlanner(tmp_path).preflight(
            EvolutionExecutionPlanSpec.load(config)
        )

    payload["budgets"]["validation_search"]["max_agent_runs"] = 100
    payload["datasets"]["locked_test"]["split_plan_sha256"] = "e" * 64
    config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(EvolutionExecutionPlanError, match="same split plan"):
        EvolutionExecutionPlanSpec.load(config)


def test_cli_preflight_prepare_inspect_verify_and_tamper_detection(
    tmp_path: Path, proposal_stub: RealLLMProposalResult
) -> None:
    config = _config(tmp_path, proposal_stub)
    preflight = runner.invoke(app, ["evolution", "plan", "preflight", str(config)])
    assert preflight.exit_code == 0, preflight.output
    assert json.loads(preflight.stdout)["total_agent_runs"] == 102

    workspace = tmp_path / "workspace"
    prepared = runner.invoke(
        app,
        [
            "evolution",
            "plan",
            "prepare",
            str(config),
            "--workspace",
            str(workspace),
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    summary = json.loads(prepared.stdout)
    assert summary["real_calls_executed"] is False
    directory = Path(summary["directory"])

    inspected = runner.invoke(app, ["evolution", "plan", "inspect", str(directory)])
    assert inspected.exit_code == 0, inspected.output
    verified = runner.invoke(app, ["evolution", "plan", "verify", str(directory)])
    assert verified.exit_code == 0, verified.output

    report = directory / "execution-plan.md"
    report.write_text(report.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
    tampered = runner.invoke(app, ["evolution", "plan", "verify", str(directory)])
    assert tampered.exit_code != 0
    assert "artifact mismatch" in tampered.output
