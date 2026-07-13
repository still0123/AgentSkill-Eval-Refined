"""Strict, auditable contracts for MCP tool evaluation."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional, Tuple
from uuid import UUID

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SideEffectClass(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


class FailureKind(str, Enum):
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RATE_LIMIT = "rate_limit"
    MALFORMED_RESPONSE = "malformed_response"
    UNAVAILABLE = "unavailable"
    PARTIAL_RESULT = "partial_result"


class ToolDefinition(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    input_schema: Dict[str, JsonValue]
    side_effect: SideEffectClass
    sensitive_parameters: Tuple[str, ...] = ()
    forbidden_parameters: Tuple[str, ...] = ()

    @field_validator("input_schema")
    @classmethod
    def schema_must_be_valid(cls, value: Dict[str, JsonValue]) -> Dict[str, JsonValue]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            raise ValueError(f"invalid JSON schema: {error.message}") from error
        return value


class ParameterRequirement(StrictModel):
    tool: str
    parameters: Tuple[str, ...]


class ParameterConstraint(StrictModel):
    tool: str
    parameter: str
    rule: Literal["equals", "not_equals", "contains", "max_length"]
    value: JsonValue


class SequenceRule(StrictModel):
    tools: Tuple[str, ...] = Field(min_length=1)


class RecoveryExpectation(StrictModel):
    tool: str
    failure: FailureKind
    action: Literal["retry", "correct_parameters", "fallback", "report_failure"]
    max_retries: int = Field(default=1, ge=0, le=10)
    fallback_tool: Optional[str] = None


class SideEffectPolicy(StrictModel):
    allow_mutating: bool = False
    allow_destructive: bool = False
    require_confirmation: bool = True
    confirmation_token: Optional[str] = None

    @model_validator(mode="after")
    def confirmation_is_required_for_destructive_access(self) -> "SideEffectPolicy":
        if self.allow_destructive and not self.confirmation_token:
            raise ValueError("destructive access requires a confirmation_token")
        return self


class Oracle(StrictModel):
    final_status: Literal["success", "failure"]
    required_response_contains: Tuple[str, ...] = ()


class Provenance(StrictModel):
    source: str = Field(min_length=1)
    author: str = Field(min_length=1)
    version: str = Field(min_length=1)


class McpCase(StrictModel):
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    task: str = Field(min_length=1)
    available_tools: Tuple[ToolDefinition, ...] = Field(min_length=1)
    expected_tools: Tuple[str, ...]
    forbidden_tools: Tuple[str, ...] = ()
    required_parameters: Tuple[ParameterRequirement, ...] = ()
    parameter_constraints: Tuple[ParameterConstraint, ...] = ()
    allowed_sequences: Tuple[SequenceRule, ...] = ()
    forbidden_sequences: Tuple[SequenceRule, ...] = ()
    expected_recovery: Tuple[RecoveryExpectation, ...] = ()
    max_tool_calls: int = Field(ge=0, le=100)
    side_effect_policy: SideEffectPolicy
    oracle: Oracle
    independence_group: str = Field(min_length=1)
    provenance: Provenance
    simulated: Literal[True]

    @model_validator(mode="after")
    def references_and_safety_must_be_valid(self) -> "McpCase":
        names = [tool.name for tool in self.available_tools]
        if len(names) != len(set(names)):
            raise ValueError("available tool names must be unique")
        known = set(names)
        expected = set(self.expected_tools)
        forbidden = set(self.forbidden_tools)
        if len(self.expected_tools) != len(expected) or len(self.forbidden_tools) != len(forbidden):
            raise ValueError("expected and forbidden tool names must be unique")
        if not expected <= known:
            raise ValueError(f"expected tools do not exist: {sorted(expected - known)}")
        if expected & forbidden:
            raise ValueError(
                f"expected and forbidden tools conflict: {sorted(expected & forbidden)}"
            )
        referenced = {
            *(item.tool for item in self.required_parameters),
            *(item.tool for item in self.parameter_constraints),
            *(item.tool for item in self.expected_recovery),
        }
        sequence_tools = {
            name
            for rule in (*self.allowed_sequences, *self.forbidden_sequences)
            for name in rule.tools
        }
        if not referenced | sequence_tools <= known:
            raise ValueError("case rules reference an unavailable tool")
        for tool in self.available_tools:
            if tool.side_effect != SideEffectClass.READ_ONLY:
                permitted = self.side_effect_policy.allow_mutating
                if tool.side_effect == SideEffectClass.DESTRUCTIVE:
                    permitted = self.side_effect_policy.allow_destructive
                if not permitted and tool.name not in forbidden:
                    raise ValueError(
                        f"mutating tool {tool.name!r} is neither allowed nor forbidden"
                    )
        return self


class McpDataset(StrictModel):
    name: str = Field(min_length=1)
    cases: Tuple[McpCase, ...] = Field(min_length=1)
    simulated: Literal[True]

    @model_validator(mode="after")
    def case_ids_must_be_unique(self) -> "McpDataset":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique")
        return self

    @classmethod
    def load(cls, path: Path, *, allowed_root: Optional[Path] = None) -> "McpDataset":
        safe = secure_input_path(path, allowed_root or path.parent)
        raw = yaml.safe_load(safe.read_text(encoding="utf-8"))
        return cls.model_validate(raw)


def secure_input_path(path: Path, allowed_root: Path) -> Path:
    """Reject symlinks and paths escaping the explicitly allowed dataset root."""
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError("symbolic-link inputs are not allowed")
    resolved = expanded.resolve(strict=True)
    root = allowed_root.expanduser().resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise ValueError("input path escapes the allowed root")
    if not resolved.is_file():
        raise ValueError("dataset input must be a regular file")
    return resolved


class McpEventKind(str, Enum):
    SERVER_CONNECTED = "mcp.server.connected"
    TOOLS_LISTED = "mcp.tools.listed"
    TOOL_REQUESTED = "mcp.tool.requested"
    TOOL_SUCCEEDED = "mcp.tool.succeeded"
    TOOL_FAILED = "mcp.tool.failed"
    TOOL_TIMEOUT = "mcp.tool.timeout"
    TOOL_RETRIED = "mcp.tool.retried"
    TOOL_CANCELLED = "mcp.tool.cancelled"
    SIDE_EFFECT_REQUESTED = "mcp.side_effect.requested"
    SIDE_EFFECT_CONFIRMED = "mcp.side_effect.confirmed"
    SIDE_EFFECT_REJECTED = "mcp.side_effect.rejected"


class McpTraceEvent(StrictModel):
    attempt_id: UUID
    sequence: int = Field(ge=1)
    timestamp: datetime
    kind: McpEventKind
    server_identity: str = Field(min_length=1)
    tool_name: Optional[str] = None
    arguments_summary: Dict[str, JsonValue] = Field(default_factory=dict)
    response_status: Optional[str] = None
    latency_ms: float = Field(default=0, ge=0)
    error_category: Optional[FailureKind] = None
    retry_number: int = Field(default=0, ge=0)
    side_effect: Optional[SideEffectClass] = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamps must be timezone-aware")
        return value


class McpTrace(StrictModel):
    run_id: UUID
    case_id: str
    simulated: bool
    events: Tuple[McpTraceEvent, ...]

    @model_validator(mode="after")
    def sequences_are_contiguous(self) -> "McpTrace":
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("trace event sequences must be contiguous and start at one")
        return self


SECRET_MARKERS = ("secret", "token", "password", "authorization", "api_key", "credential")


def redact_arguments(
    arguments: Mapping[str, Any], sensitive_parameters: Tuple[str, ...] = ()
) -> Dict[str, JsonValue]:
    sensitive = {item.lower() for item in sensitive_parameters}
    result: Dict[str, JsonValue] = {}
    for key, value in arguments.items():
        lowered = key.lower()
        if lowered in sensitive or any(marker in lowered for marker in SECRET_MARKERS):
            result[key] = "[REDACTED]"
        else:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            result[key] = encoded[:200]
    return result
