"""MCP case and normalized trace contract tests."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from agentskill_eval_mcp_lab import McpDataset, McpTrace, McpTraceEvent, redact_arguments
from agentskill_eval_mcp_lab.contracts import McpEventKind

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "examples/mcp/dataset.yaml"


def _raw_dataset() -> dict[str, object]:
    loaded = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_case_schema_validates_complete_demo() -> None:
    dataset = McpDataset.load(DATASET, allowed_root=DATASET.parent)
    assert len(dataset.cases) == 2
    assert dataset.simulated is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda case: case["available_tools"].append(case["available_tools"][0]), "unique"),
        (lambda case: case["expected_tools"].append("missing_tool"), "do not exist"),
        (lambda case: case["forbidden_tools"].append("search_documents"), "conflict"),
        (lambda case: case.update({"oracle": None}), "oracle"),
        (
            lambda case: case["available_tools"][0].update(
                {"input_schema": {"type": "definitely-not-a-json-schema-type"}}
            ),
            "schema",
        ),
    ],
)
def test_invalid_case_contracts_are_rejected(mutation: object, message: str) -> None:
    raw = _raw_dataset()
    case = raw["cases"][0]  # type: ignore[index]
    mutation(case)  # type: ignore[operator]
    with pytest.raises((ValidationError, ValueError), match=message):
        McpDataset.model_validate(raw)


def test_unrestricted_mutating_tool_is_rejected() -> None:
    raw = _raw_dataset()
    case = raw["cases"][0]  # type: ignore[index]
    case["forbidden_tools"].remove("create_ticket")
    with pytest.raises(ValidationError, match="neither allowed nor forbidden"):
        McpDataset.model_validate(raw)


def test_dataset_loader_rejects_symlink_and_path_escape(tmp_path: Path) -> None:
    target = tmp_path / "dataset.yaml"
    target.write_text(DATASET.read_text(encoding="utf-8"), encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic-link"):
        McpDataset.load(link, allowed_root=tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        McpDataset.load(DATASET, allowed_root=tmp_path)


def test_secret_arguments_are_redacted() -> None:
    summary = redact_arguments({"query": "safe", "api_token": "top-secret", "password": "x"})
    assert summary == {"query": '"safe"', "api_token": "[REDACTED]", "password": "[REDACTED]"}
    assert "top-secret" not in str(summary)


def test_trace_sequence_must_be_contiguous() -> None:
    attempt = uuid4()
    with pytest.raises(ValidationError, match="contiguous"):
        McpTrace(
            run_id=uuid4(),
            case_id="case",
            simulated=True,
            events=(
                McpTraceEvent(
                    attempt_id=attempt,
                    sequence=2,
                    timestamp="2024-01-01T00:00:00Z",  # type: ignore[arg-type]
                    kind=McpEventKind.SERVER_CONNECTED,
                    server_identity="mock",
                ),
            ),
        )
