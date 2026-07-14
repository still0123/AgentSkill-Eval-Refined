"""End-to-end failure diagnosis to frozen Skill candidate evolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click import unstyle
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import CandidateOrigin
from agentskill_eval_skill_optimizer import (
    EvolutionError,
    FailureGuidedEvolutionSpec,
    FailureGuidedSkillEvolution,
    HypothesisGeneratorSpec,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/optimizer/failure-guided/evolution.example.yaml"
PROCESS_EXAMPLE = ROOT / "examples/optimizer/failure-guided/process-evolution.example.yaml"
runner = CliRunner()


def test_failure_diagnoses_generate_search_candidates_and_frozen_handoff(
    tmp_path: Path,
) -> None:
    spec = FailureGuidedEvolutionSpec.load(EXAMPLE)
    service = FailureGuidedSkillEvolution(tmp_path / "workspace")
    result = service.run(spec)
    replay = service.run(spec)

    assert result.report == replay.report
    assert result.search.job.locked_test_accessed is False
    assert result.search.winner.origin == CandidateOrigin.SEARCH
    assert len(result.report.hypotheses) == 4
    assert result.report.candidate_count == 7
    assert result.report.winner_skill_sha256 == result.search.winner.content_sha256
    assert result.report.regression_gate.passed is True
    assert result.report.regression_gate.loss_cases == ()
    assert result.report.regression_gate.base.stage.value == "regression_dev"
    assert result.report.regression_gate.winner.stage.value == "regression_dev"
    context = json.loads(
        Path(result.report.artifacts["optimization_context"]).read_text(encoding="utf-8")
    )
    assert len(context["eligible"]) == 4
    assert len(context["excluded"]) == 1
    assert context["excluded"][0]["label"] == "ENVIRONMENT"
    assert context["locked_test_accessed"] is False
    assert context["raw_rationale_stored"] is False
    handoff = json.loads(result.handoff_path.read_text(encoding="utf-8"))
    assert handoff["status"] == "AWAITING_INDEPENDENT_FINAL_EVALUATION"
    assert handoff["locked_test_accessed"] is False
    assert handoff["auto_publish"] is False
    assert Path(result.report.artifacts["regression_gate"]).is_file()
    assert "locked_test" not in FailureGuidedEvolutionSpec.model_fields


def test_raw_rationale_and_secret_are_not_persisted(tmp_path: Path) -> None:
    payload = yaml.safe_load(
        (ROOT / "examples/optimizer/failure-guided/train-failures.yaml").read_text(encoding="utf-8")
    )
    secret = "private-optimizer-secret-value"
    payload["diagnoses"][0]["findings"][0]["rationale"] += f" Secret={secret}"
    bundle = tmp_path / "train-failures.yaml"
    bundle.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    spec = FailureGuidedEvolutionSpec.load(EXAMPLE).model_copy(
        update={"failure_bundle_path": bundle}
    )
    result = FailureGuidedSkillEvolution(tmp_path / "workspace").run(spec)
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in result.report_json.parent.rglob("*")
        if path.is_file()
    )
    assert secret not in persisted
    assert "The Agent compared values" not in persisted


def test_evolution_rejects_non_train_and_insufficient_failure_labels(tmp_path: Path) -> None:
    source = yaml.safe_load(
        (ROOT / "examples/optimizer/failure-guided/train-failures.yaml").read_text(encoding="utf-8")
    )
    source["split"] = "locked_test"
    invalid = tmp_path / "locked.yaml"
    invalid.write_text(yaml.safe_dump(source), encoding="utf-8")
    spec = FailureGuidedEvolutionSpec.load(EXAMPLE).model_copy(
        update={"failure_bundle_path": invalid}
    )
    with pytest.raises(EvolutionError, match="invalid train failure bundle"):
        FailureGuidedSkillEvolution(tmp_path / "workspace-a").run(spec)

    source["split"] = "train"
    source["diagnoses"] = source["diagnoses"][:2]
    insufficient = tmp_path / "insufficient.yaml"
    insufficient.write_text(yaml.safe_dump(source), encoding="utf-8")
    spec = spec.model_copy(update={"failure_bundle_path": insufficient})
    with pytest.raises(EvolutionError, match="at least three distinct"):
        FailureGuidedSkillEvolution(tmp_path / "workspace-b").run(spec)


def test_evolution_cli_requires_simulation_opt_in_and_status_is_read_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    denied = runner.invoke(
        app,
        ["optimize", "evolve", "run", str(EXAMPLE), "--workspace", str(workspace)],
    )
    assert denied.exit_code != 0
    normalized_output = "".join(
        character
        for character in unstyle(denied.output)
        if character.isalnum() or character == "-"
    )
    assert "simulatedevaluatorrequires" in normalized_output
    assert "--allow-simulation" in normalized_output

    allowed = runner.invoke(
        app,
        [
            "optimize",
            "evolve",
            "run",
            str(EXAMPLE),
            "--workspace",
            str(workspace),
            "--allow-simulation",
        ],
    )
    assert allowed.exit_code == 0, allowed.output
    summary = json.loads(allowed.output)
    assert summary["locked_test_accessed"] is False
    status = runner.invoke(
        app,
        ["optimize", "evolve", "status", str(workspace), summary["evolution_id"]],
    )
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["evolution_id"] == summary["evolution_id"]


def test_evolution_artifacts_are_immutable_and_html_is_escaped(tmp_path: Path) -> None:
    spec = FailureGuidedEvolutionSpec.load(EXAMPLE).model_copy(
        update={"name": '<script>alert("evolution")</script>'}
    )
    service = FailureGuidedSkillEvolution(tmp_path / "workspace")
    result = service.run(spec)
    rendered = result.report_html.read_text(encoding="utf-8")
    assert '<script>alert("evolution")</script>' not in rendered
    assert "&lt;script&gt;alert" in rendered
    assert "default-src 'none'" in rendered

    context_path = Path(result.report.artifacts["optimization_context"])
    context_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(EvolutionError, match="immutable evolution artifact changed"):
        service.run(spec)


def test_evolution_service_cannot_bypass_real_evaluator_safety_gate(tmp_path: Path) -> None:
    spec = FailureGuidedEvolutionSpec.load(EXAMPLE)
    unsafe = spec.model_copy(
        update={"evaluator": spec.evaluator.model_copy(update={"simulated": False})}
    )
    with pytest.raises(EvolutionError, match="confirmation and budget protocol"):
        FailureGuidedSkillEvolution(tmp_path / "workspace").run(unsafe)


def test_process_generator_is_audited_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv("ASE_PROCESS_GENERATOR_COUNTER", str(counter))
    spec = FailureGuidedEvolutionSpec.load(PROCESS_EXAMPLE)
    generator = spec.generator.model_copy(
        update={
            "allowed_environment": (
                "PATH",
                "LANG",
                "LC_ALL",
                "ASE_PROCESS_GENERATOR_COUNTER",
            )
        }
    )
    spec = spec.model_copy(update={"generator": generator})
    service = FailureGuidedSkillEvolution(tmp_path / "workspace")
    result = service.run(spec)
    replay = service.run(spec)

    assert result.report == replay.report
    assert counter.read_text(encoding="utf-8") == "1"
    evidence = result.report.generator_evidence
    assert evidence is not None
    assert evidence.version_verified is True
    assert evidence.hypothesis_count == 4
    assert evidence.raw_request_stored is False
    assert evidence.raw_response_stored is False
    assert evidence.stderr_stored is False
    assert evidence.hidden_reasoning_stored is False
    assert result.report.generator_identity.startswith("process-hypothesis-generator:")
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in result.report_json.parent.rglob("*")
        if path.is_file()
    )
    assert '"base_skill"' not in persisted
    assert "The Agent compared values" not in persisted


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("invalid-json", "returned invalid JSON"),
        ("ineligible", "ineligible failure label"),
        ("timeout", "timed out"),
    ],
)
def test_process_generator_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: str,
) -> None:
    monkeypatch.setenv("ASE_FAKE_GENERATOR_MODE", mode)
    spec = FailureGuidedEvolutionSpec.load(PROCESS_EXAMPLE)
    generator = spec.generator.model_copy(
        update={
            "allowed_environment": ("PATH", "ASE_FAKE_GENERATOR_MODE"),
            "timeout_seconds": 0.1,
        }
    )
    spec = spec.model_copy(update={"generator": generator})
    with pytest.raises(EvolutionError, match=expected):
        FailureGuidedSkillEvolution(tmp_path / "workspace").run(spec)


def test_process_generator_rejects_hash_mismatch_and_secret_environment(tmp_path: Path) -> None:
    spec = FailureGuidedEvolutionSpec.load(PROCESS_EXAMPLE)
    mismatched = spec.model_copy(
        update={
            "generator": spec.generator.model_copy(update={"expected_sha256": "0" * 64})
        }
    )
    with pytest.raises(EvolutionError, match="SHA-256 mismatch"):
        FailureGuidedSkillEvolution(tmp_path / "workspace").run(mismatched)

    payload = spec.generator.model_dump(mode="python")
    payload["allowed_environment"] = ("PATH", "OPENAI_API_KEY")
    with pytest.raises(ValueError, match="Secret-like"):
        HypothesisGeneratorSpec.model_validate(payload)

    payload["allowed_environment"] = ("PATH", "HOME")
    with pytest.raises(ValueError, match="minimal allowlist"):
        HypothesisGeneratorSpec.model_validate(payload)


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"max_request_bytes": 10}, "request is too large"),
        ({"max_response_bytes": 10}, "response is too large"),
        ({"expected_version_output": "wrong-version"}, "version mismatch"),
    ],
)
def test_process_generator_enforces_frozen_io_and_version_limits(
    tmp_path: Path, update: dict[str, object], expected: str
) -> None:
    spec = FailureGuidedEvolutionSpec.load(PROCESS_EXAMPLE)
    spec = spec.model_copy(
        update={"generator": spec.generator.model_copy(update=update)}
    )
    with pytest.raises(EvolutionError, match=expected):
        FailureGuidedSkillEvolution(tmp_path / "workspace").run(spec)
