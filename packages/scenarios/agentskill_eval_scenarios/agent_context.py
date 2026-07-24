"""Whitelisted case views sent to Process Scenario Agents."""

from __future__ import annotations

from typing import Dict, Literal, Optional, Tuple

from pydantic import Field, JsonValue

from agentskill_eval_contracts import FrozenModel
from agentskill_eval_mcp_lab import McpCase
from agentskill_eval_memory_rag_lab import MemoryRagCase


class McpAgentCaseInput(FrozenModel):
    """Agent-visible MCP task constraints, excluding grader-only fields."""

    task: str = Field(min_length=1)
    available_tools: Tuple[Dict[str, JsonValue], ...] = Field(min_length=1)
    max_tool_calls: int = Field(ge=0)
    side_effect_policy: Dict[str, JsonValue]

    @classmethod
    def from_case(cls, case: McpCase) -> "McpAgentCaseInput":
        return cls(
            task=case.task,
            available_tools=tuple(
                tool.model_dump(mode="json") for tool in case.available_tools
            ),
            max_tool_calls=case.max_tool_calls,
            side_effect_policy=case.side_effect_policy.model_dump(mode="json"),
        )


class MemoryRagAgentDocument(FrozenModel):
    """Document content visible to the Agent, without evaluator annotations."""

    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class MemoryRagAgentMemoryPolicy(FrozenModel):
    forbidden_keys: Tuple[str, ...] = ()
    sensitive_keys: Tuple[str, ...] = ()


class MemoryRagAgentCaseInput(FrozenModel):
    """Agent-visible Memory/RAG task constraints, excluding grader-only fields."""

    pair_type: str = Field(min_length=1)
    task: str = Field(min_length=1)
    kind: Literal["retrieval_generation", "memory"]
    query: Optional[str] = None
    k: int = Field(ge=1)
    documents: Tuple[MemoryRagAgentDocument, ...] = ()
    memory_policy: MemoryRagAgentMemoryPolicy

    @classmethod
    def from_case(
        cls, case: MemoryRagCase, pair_type: str
    ) -> "MemoryRagAgentCaseInput":
        return cls(
            pair_type=pair_type,
            task=case.task,
            kind=case.kind,
            query=case.query,
            k=case.k,
            documents=tuple(
                MemoryRagAgentDocument(document_id=item.document_id, text=item.text)
                for item in case.documents
            ),
            memory_policy=MemoryRagAgentMemoryPolicy(
                forbidden_keys=case.forbidden_memory_keys,
                sensitive_keys=case.sensitive_memory_keys,
            ),
        )
