from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agentskill_eval_contracts import ArtifactEntry, ArtifactManifest
from agentskill_eval_experiment.storage import ExperimentLayout
from agentskill_eval_skill_optimizer import (
    FailureBridgeError,
    ObservedFailureEvidenceBridge,
)


def _session_fixture(
    tmp_path: Path,
    tool_calls: list[tuple[str, object]],
) -> tuple[ObservedFailureEvidenceBridge, UUID, UUID, Path]:
    bridge = ObservedFailureEvidenceBridge(tmp_path)
    experiment_id = uuid4()
    run_id = uuid4()
    attempt_id = uuid4()
    transcript = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        }
        for name, arguments in tool_calls
    ]
    content = json.dumps(
        {
            "tool_calls": len(tool_calls),
            "transcript": transcript,
            "artifacts": {
                "workspace_diff": "case-specific content that must not be inspected"
            },
        }
    ).encode()
    relative = (
        "raw-runner/case/with_skill/outputs/agent/run/session-result.json"
    )
    layout = ExperimentLayout(tmp_path, experiment_id)
    session_path = layout.attempt_root(run_id, 1) / relative
    session_path.parent.mkdir(parents=True)
    session_path.write_bytes(content)
    bridge.store.save_artifact_manifest(
        experiment_id,
        run_id,
        1,
        attempt_id,
        ArtifactManifest(
            artifacts=(
                ArtifactEntry(
                    path=relative,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    media_type="application/json",
                ),
            )
        ),
    )
    return bridge, experiment_id, run_id, session_path


def test_behavior_summary_reports_only_fully_observed_read_only_actions(
    tmp_path: Path,
) -> None:
    bridge, experiment_id, run_id, _ = _session_fixture(
        tmp_path,
        [
            ("read_file", json.dumps({"path": "private-case-path.py"})),
            ("run_command", json.dumps({"command": "grep -n target module.py | head"})),
            (
                "run_command",
                json.dumps({"command": r"grep 'class name\|def method' module.py | head"}),
            ),
            ("run_command", json.dumps({"command": "sed -n '1,80p' module.py"})),
        ],
    )

    summary = bridge._observed_behavior_summary(experiment_id, run_id, 1)

    assert "only read-only inspection actions" in summary
    assert "no edit action or test-oriented command" in summary
    assert "private-case-path" not in summary
    assert "module.py" not in summary
    assert "case-specific content" not in summary


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("run_command", json.dumps({"command": "python -m pytest tests/test_bug.py"})),
        ("run_command", json.dumps({"command": "python mutate_repository.py"})),
        ("run_command", json.dumps({"command": "find . -name '*.pyc' -delete"})),
        ("run_command", json.dumps({"command": "sed -i.bak 's/a/b/' module.py"})),
        ("unrecognized_tool", {}),
    ],
)
def test_behavior_summary_abstains_when_negative_evidence_is_not_complete(
    tmp_path: Path,
    name: str,
    arguments: object,
) -> None:
    bridge, experiment_id, run_id, _ = _session_fixture(
        tmp_path, [(name, arguments)]
    )

    assert bridge._observed_behavior_summary(experiment_id, run_id, 1) == ""


def test_behavior_summary_rejects_tampered_session_artifact(tmp_path: Path) -> None:
    bridge, experiment_id, run_id, session_path = _session_fixture(
        tmp_path,
        [("read_file", json.dumps({"path": "module.py"}))],
    )
    session_path.write_text("{}", encoding="utf-8")

    with pytest.raises(FailureBridgeError, match="integrity mismatch"):
        bridge._observed_behavior_summary(experiment_id, run_id, 1)
