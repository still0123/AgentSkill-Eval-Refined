"""Contract tests for the unified multi-scenario facade."""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentskill_eval_mcp_lab import McpDataset
from agentskill_eval_mcp_lab.adapters import MockMcpAdapter
from agentskill_eval_scenarios import (
    ComparisonKind,
    EvidenceClass,
    ProcessAgentError,
    ProcessScenarioAgentClient,
    ProcessScenarioAgentSpec,
    UnifiedScenarioRunner,
    UnifiedScenarioSpec,
)
from agentskill_eval_scenarios.interactive import InteractionHistoryEvent, InteractiveAgentAction
from agentskill_eval_scenarios.interactive_runtime import InteractiveMcpController

ROOT = Path(__file__).resolve().parents[2]
FAKE_AGENT = ROOT / "tests/fixtures/fake_scenario_agent.py"


def test_skill_ab_requires_frozen_skill() -> None:
    with pytest.raises(ValidationError, match="frozen Skill"):
        UnifiedScenarioSpec.model_validate(
            {
                "schema_version": "ase/unified-scenario/v1alpha1",
                "name": "missing-skill",
                "scenario": "mcp_tool",
                "comparison": ComparisonKind.SKILL_AB,
                "native_config": "config.yaml",
                "simulated": True,
                "evidence_class": EvidenceClass.SIMULATED_CONTROLLER,
                "claim_limit": "simulation only",
            }
        )


def test_observed_evidence_cannot_be_marked_simulated() -> None:
    payload = UnifiedScenarioSpec.load(ROOT / "examples/unified/mcp-tool.yaml").model_dump()
    payload["evidence_class"] = EvidenceClass.OBSERVED_AGENT
    with pytest.raises(ValidationError, match="cannot be simulated"):
        UnifiedScenarioSpec.model_validate(payload)


def test_skill_hash_is_verified() -> None:
    spec = UnifiedScenarioSpec.load(ROOT / "examples/unified/mcp-tool.yaml")
    assert spec.skill is not None
    broken = spec.skill.model_copy(update={"expected_sha256": "0" * 64})
    with pytest.raises(ValueError, match="does not match"):
        broken.verify()


def test_adapter_rejects_evidence_boundary_different_from_native_runner() -> None:
    spec = UnifiedScenarioSpec.load(ROOT / "examples/unified/mcp-tool.yaml")
    mismatched = spec.model_copy(
        update={"simulated": False, "evidence_class": EvidenceClass.PROCESS_INTEGRATION}
    )
    with pytest.raises(ValueError, match="evidence boundary"):
        UnifiedScenarioRunner(ROOT).validate(mismatched)


def _agent_spec(executable: Path = FAKE_AGENT, **updates: object) -> ProcessScenarioAgentSpec:
    payload: dict[str, object] = {
        "name": "fake-scenario-agent",
        "version": "1.0.0",
        "executable": executable,
        "expected_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "expected_version_output": "fake-scenario-agent 1.0.0",
        "timeout_seconds": 1,
    }
    payload.update(updates)
    return ProcessScenarioAgentSpec.model_validate(payload)


def test_process_agent_rejects_wrong_hash_and_secret_environment() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ProcessScenarioAgentClient(_agent_spec(expected_sha256="0" * 64))
    with pytest.raises(ValidationError, match="Secret-like"):
        _agent_spec(allowed_environment=("PATH", "OPENAI_API_KEY"))


def test_interactive_agent_contract_bounds_steps_and_history() -> None:
    spec = _agent_spec(
        interaction_mode="step_loop",
        max_steps=8,
        max_history_events=5,
        max_observation_bytes=4096,
    )
    assert spec.interaction_mode == "step_loop"
    assert spec.max_steps == 8
    with pytest.raises(ValidationError):
        _agent_spec(interaction_mode="step_loop", max_steps=0)
    with pytest.raises(ValidationError):
        _agent_spec(interaction_mode="step_loop", max_observation_bytes=0)


def test_process_agent_rejects_hidden_reasoning_fields(tmp_path: Path) -> None:
    executable = tmp_path / "bad-agent"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ['--version']:
    print('fake-scenario-agent 1.0.0')
else:
    json.load(sys.stdin)
    response = {'schema_version':'ase/process-agent-response/v1alpha1',
                'plan':{},'reasoning':'x'}
    json.dump(response, sys.stdout)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    client = ProcessScenarioAgentClient(_agent_spec(executable))
    dataset = McpDataset.load(ROOT / "examples/mcp/dataset.yaml")
    with pytest.raises(ProcessAgentError, match="only schema_version and plan"):
        client.decide_mcp(dataset.cases[0], "without_guidance", None)


def test_interactive_process_agent_rejects_hidden_reasoning_fields(tmp_path: Path) -> None:
    executable = tmp_path / "bad-interactive-agent"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ['--version']:
    print('fake-scenario-agent 1.0.0')
else:
    json.load(sys.stdin)
    response = {'schema_version':'ase/interactive-agent-response/v1alpha1',
                'action':{'kind':'final','answer':'done'},'reasoning':'x'}
    json.dump(response, sys.stdout)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    client = ProcessScenarioAgentClient(_agent_spec(executable, interaction_mode="step_loop"))
    history = (
        InteractionHistoryEvent(
            step=1,
            action=InteractiveAgentAction(kind="tool_call", tool="search", arguments={}),
            observation={"ok": True, "status": "ok"},
        ),
    )
    with pytest.raises(ProcessAgentError, match="only schema_version and action"):
        client.next_action(
            "mcp_tool", "case", "without_guidance", {"task": "test"}, history, None, 2
        )


def test_interactive_controller_stops_at_frozen_step_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_SCENARIO_AGENT_FORCE_LOOP", "1")
    client = ProcessScenarioAgentClient(
        _agent_spec(
            interaction_mode="step_loop",
            max_steps=2,
            allowed_environment=("PATH", "FAKE_SCENARIO_AGENT_FORCE_LOOP"),
        )
    )
    case = McpDataset.load(ROOT / "examples/mcp/dataset.yaml").cases[0]
    outcome, evidence = InteractiveMcpController().run(
        case, MockMcpAdapter(case.available_tools), client, "without_guidance", None
    )
    assert outcome.budget_exhausted is True
    assert outcome.completed is False
    assert evidence.termination == "step_limit"
    assert evidence.events[-1].kind == "agent.step_limit"


def test_process_agent_terminates_timeout(tmp_path: Path) -> None:
    executable = tmp_path / "slow-agent"
    executable.write_text(
        """#!/usr/bin/env python3
import sys, time
if sys.argv[1:] == ['--version']:
    print('fake-scenario-agent 1.0.0')
else:
    time.sleep(10)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    client = ProcessScenarioAgentClient(_agent_spec(executable, timeout_seconds=0.5))
    dataset = McpDataset.load(ROOT / "examples/mcp/dataset.yaml")
    with pytest.raises(ProcessAgentError, match="timed out"):
        client.decide_mcp(dataset.cases[0], "without_guidance", None)
