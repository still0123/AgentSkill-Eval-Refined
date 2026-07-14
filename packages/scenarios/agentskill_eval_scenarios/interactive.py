"""Versioned contracts for bounded, auditable Agent/environment interaction loops."""

from __future__ import annotations

from typing import Dict, Literal, Optional, Tuple

from pydantic import Field, JsonValue, model_validator

from agentskill_eval_contracts import FrozenModel


class InteractiveAgentAction(FrozenModel):
    """One externally observable Agent action; hidden reasoning is never accepted."""

    kind: Literal["tool_call", "retrieve", "memory", "final"]
    tool: Optional[str] = None
    arguments: Dict[str, JsonValue] = Field(default_factory=dict)
    confirmation_token: Optional[str] = None
    query: Optional[str] = None
    k: Optional[int] = Field(default=None, ge=1, le=100)
    mode: Optional[Literal["ranked", "clean", "noisy"]] = None
    operation: Optional[
        Literal["write", "read", "update", "overwrite", "delete", "forget", "expire"]
    ] = None
    session_id: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    ttl_steps: Optional[int] = Field(default=None, ge=1, le=1000)
    answer: Optional[str] = None
    citations: Tuple[str, ...] = ()
    claims: Tuple[Dict[str, JsonValue], ...] = ()
    token_count: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def fields_must_match_kind(self) -> "InteractiveAgentAction":
        required = {
            "tool_call": bool(self.tool),
            "retrieve": bool(self.query) and self.k is not None and self.mode is not None,
            "memory": bool(self.operation and self.session_id and self.key),
            "final": self.answer is not None,
        }
        if not required[self.kind]:
            raise ValueError(f"missing required fields for {self.kind} action")
        return self


class InteractionHistoryEvent(FrozenModel):
    """Bounded event sent back to the next isolated Agent process."""

    step: int = Field(ge=1)
    action: InteractiveAgentAction
    observation: Dict[str, JsonValue]


class InteractiveTraceEvent(FrozenModel):
    """Persisted redacted evidence. Raw document and memory values are never stored here."""

    sequence: int = Field(ge=1)
    step: int = Field(ge=0)
    kind: Literal[
        "agent.session.started",
        "skill.activated",
        "agent.decision.requested",
        "agent.action.proposed",
        "environment.action.accepted",
        "environment.action.rejected",
        "environment.observation",
        "agent.final",
        "agent.step_limit",
        "agent.timeout",
        "agent.cancelled",
    ]
    action_kind: Optional[str] = None
    target: Optional[str] = None
    status: Optional[str] = None
    payload_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class InteractiveRunEvidence(FrozenModel):
    schema_version: Literal["ase/interactive-run-evidence/v1alpha1"] = (
        "ase/interactive-run-evidence/v1alpha1"
    )
    scenario: Literal["mcp_tool", "memory_rag"]
    case_id: str = Field(min_length=1)
    variant: str = Field(min_length=1)
    skill_present: bool
    skill_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    max_steps: int = Field(ge=1, le=50)
    completed: bool
    termination: Literal["final", "step_limit", "error"]
    events: Tuple[InteractiveTraceEvent, ...]
    hidden_reasoning_stored: Literal[False] = False
