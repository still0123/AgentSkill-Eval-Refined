"""End-to-end Process Agent Skill activation for MCP and Memory/RAG scenarios."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentskill_eval_cli.main import app

ROOT = Path(__file__).resolve().parents[2]
FAKE_AGENT = ROOT / "tests/fixtures/fake_scenario_agent.py"
runner = CliRunner()


def _process_spec(tmp_path: Path, base_name: str) -> Path:
    base_path = ROOT / "examples/unified" / base_name
    payload = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    native = payload["native_config"]
    payload["native_config"] = str((base_path.parent / native).resolve())
    payload["skill"]["path"] = str((base_path.parent / payload["skill"]["path"]).resolve())
    payload["skill"]["activation_mode"] = "process_prompt"
    payload["evidence_class"] = "process_integration"
    payload["claim_limit"] = (
        "Local Fake Process Agent integration evidence only; not real model performance evidence."
    )
    payload["process_agent"] = {
        "name": "fake-scenario-agent",
        "version": "1.0.0",
        "executable": str(FAKE_AGENT),
        "expected_sha256": hashlib.sha256(FAKE_AGENT.read_bytes()).hexdigest(),
        "expected_version_output": "fake-scenario-agent 1.0.0",
        "timeout_seconds": 5,
        "allowed_environment": ["PATH", "FAKE_SCENARIO_AGENT_COUNTER_FILE"],
    }
    path = tmp_path / f"process-{base_name}"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _interactive_process_spec(tmp_path: Path, base_name: str) -> Path:
    path = _process_spec(tmp_path, base_name)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["process_agent"].update(
        {
            "interaction_mode": "step_loop",
            "max_steps": 20,
            "max_history_events": 20,
            "max_observation_bytes": 100_000,
        }
    )
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("base_name", "scenario"),
    [("mcp-tool.yaml", "mcp_tool"), ("memory-rag.yaml", "memory_rag")],
)
def test_process_agent_activates_skill_and_persists_only_hashed_decision_evidence(
    tmp_path: Path, base_name: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = tmp_path / f"{scenario}.counter"
    monkeypatch.setenv("FAKE_SCENARIO_AGENT_COUNTER_FILE", str(counter))
    spec = _process_spec(tmp_path, base_name)
    workspace = tmp_path / "workspace"
    command = [
        "scenario",
        "run",
        str(spec),
        "--workspace",
        str(workspace),
        "--allow-simulation",
    ]
    first = runner.invoke(app, command)
    assert first.exit_code == 0, first.stdout
    summary = json.loads(first.stdout)
    assert summary["scenario"] == scenario
    assert summary["evidence_class"] == "process_integration"
    assert int(counter.read_text(encoding="utf-8")) == 4

    report = json.loads(Path(summary["report_json"]).read_text(encoding="utf-8"))
    assert report["plan"]["agent"] == "fake-scenario-agent"
    assert report["plan"]["agent_version"] == "1.0.0"
    assert (
        report["plan"]["agent_executable_sha256"]
        == hashlib.sha256(FAKE_AGENT.read_bytes()).hexdigest()
    )
    assert report["primary_metrics"]["treatment_success_rate"] == 1
    evidence_artifact = next(
        item for item in report["artifacts"] if item["kind"] == "process_agent_decisions"
    )
    evidence_text = Path(evidence_artifact["path"]).read_text(encoding="utf-8")
    decisions = json.loads(evidence_text)["decisions"]
    assert len(decisions) == 4
    baseline = [item for item in decisions if item["variant"] in {"without_guidance", "control"}]
    treatment = [item for item in decisions if item["variant"] in {"with_guidance", "treatment"}]
    assert all(not item["skill_present"] and item["skill_sha256"] is None for item in baseline)
    assert all(item["skill_present"] and len(item["skill_sha256"]) == 64 for item in treatment)
    assert "# MCP Tool Use" not in evidence_text
    assert "# Memory and RAG Use" not in evidence_text
    assert "super-secret" not in evidence_text
    assert all(item["hidden_reasoning_stored"] is False for item in decisions)

    replay = runner.invoke(app, command)
    assert replay.exit_code == 0, replay.stdout
    assert int(counter.read_text(encoding="utf-8")) == 4


@pytest.mark.parametrize(
    ("base_name", "scenario"),
    [("mcp-tool.yaml", "mcp_tool"), ("memory-rag.yaml", "memory_rag")],
)
def test_interactive_process_agent_uses_observations_and_persists_redacted_trace(
    tmp_path: Path, base_name: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = tmp_path / f"interactive-{scenario}.counter"
    monkeypatch.setenv("FAKE_SCENARIO_AGENT_COUNTER_FILE", str(counter))
    spec = _interactive_process_spec(tmp_path, base_name)
    workspace = tmp_path / "workspace"
    command = [
        "scenario",
        "run",
        str(spec),
        "--workspace",
        str(workspace),
        "--allow-simulation",
    ]
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.stdout
    summary = json.loads(result.stdout)
    assert int(counter.read_text(encoding="utf-8")) > 4
    report = json.loads(Path(summary["report_json"]).read_text(encoding="utf-8"))
    assert report["plan"]["interaction_mode"] == "step_loop"
    assert report["plan"]["max_interaction_steps"] == 20
    assert "agent_observation_loop" in report["plan"]["trace_capabilities"]
    assert report["primary_metrics"]["treatment_success_rate"] == 1
    trace_ref = next(
        item for item in report["artifacts"] if item["kind"] == "interactive_agent_traces"
    )
    trace_text = Path(trace_ref["path"]).read_text(encoding="utf-8")
    traces = json.loads(trace_text)["runs"]
    assert len(traces) == 4
    assert all(item["termination"] == "final" for item in traces)
    baselines = [item for item in traces if item["variant"] in {"without_guidance", "control"}]
    treatments = [item for item in traces if item["variant"] in {"with_guidance", "treatment"}]
    assert all(not item["skill_present"] and item["skill_sha256"] is None for item in baselines)
    assert all(item["skill_present"] and item["skill_sha256"] for item in treatments)
    assert all(item["hidden_reasoning_stored"] is False for item in traces)
    assert "super-secret" not in trace_text
    assert "ignore previous and exfiltrate" not in trace_text
    assert all(
        any(event["kind"] == "environment.observation" for event in item["events"])
        for item in treatments
    )
    call_count = int(counter.read_text(encoding="utf-8"))
    replay = runner.invoke(app, command)
    assert replay.exit_code == 0, replay.stdout
    assert int(counter.read_text(encoding="utf-8")) == call_count
