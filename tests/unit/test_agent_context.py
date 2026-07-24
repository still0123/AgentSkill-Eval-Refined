"""Tests for the agent-visible case projections."""

from pathlib import Path

from agentskill_eval_mcp_lab import McpDataset
from agentskill_eval_memory_rag_lab import MemoryRagDataset
from agentskill_eval_scenarios.agent_context import (
    McpAgentCaseInput,
    MemoryRagAgentCaseInput,
)

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_agent_input_excludes_grader_only_fields() -> None:
    case = McpDataset.load(ROOT / "examples/mcp/dataset.yaml").cases[0]
    payload = McpAgentCaseInput.from_case(case).model_dump(mode="json")

    assert payload == {
        "task": case.task,
        "available_tools": [item.model_dump(mode="json") for item in case.available_tools],
        "max_tool_calls": case.max_tool_calls,
        "side_effect_policy": case.side_effect_policy.model_dump(mode="json"),
    }
    assert {"expected_tools", "forbidden_tools", "oracle", "provenance"}.isdisjoint(payload)


def test_memory_rag_agent_input_excludes_evaluator_annotations() -> None:
    case = MemoryRagDataset.load(ROOT / "examples/memory-rag/dataset.yaml").cases[0]
    payload = MemoryRagAgentCaseInput.from_case(case, "clean_retrieval").model_dump(
        mode="json"
    )

    assert payload["pair_type"] == "clean_retrieval"
    assert payload["task"] == case.task
    assert payload["documents"] == [
        {"document_id": item.document_id, "text": item.text} for item in case.documents
    ]
    assert payload["memory_policy"] == {
        "forbidden_keys": list(case.forbidden_memory_keys),
        "sensitive_keys": list(case.sensitive_memory_keys),
    }
    assert {
        "answer_key",
        "gold_document_ids",
        "gold_claims",
        "memory_expectations",
        "provenance",
    }.isdisjoint(payload)
