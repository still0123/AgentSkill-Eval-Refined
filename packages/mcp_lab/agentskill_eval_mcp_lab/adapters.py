"""Deterministic mock and hardened local-process MCP adapter boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from agentskill_eval_mcp_lab.contracts import FailureKind, SideEffectClass, ToolDefinition


@dataclass(frozen=True)
class AdapterCapabilities:
    protocol: str
    server_identity: str
    supports_cancellation: bool
    max_response_bytes: int
    simulated: bool


@dataclass(frozen=True)
class ToolCallResult:
    ok: bool
    value: Optional[Dict[str, Any]] = None
    error: Optional[FailureKind] = None
    message: str = ""
    latency_ms: float = 0.0
    partial: bool = False


class McpAdapter(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def list_tools(self) -> Tuple[ToolDefinition, ...]: ...

    @abstractmethod
    def call(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallResult: ...


@dataclass(frozen=True)
class FailureInjection:
    tool: str
    kind: FailureKind
    fail_attempts: int = 1
    latency_ms: float = 0.0


DEFAULT_TOOLS = (
    ToolDefinition(
        name="search_documents",
        description="Search the deterministic document corpus.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        side_effect=SideEffectClass.READ_ONLY,
    ),
    ToolDefinition(
        name="get_document",
        description="Read one deterministic document.",
        input_schema={
            "type": "object",
            "properties": {"document_id": {"type": "string", "enum": ["doc-1", "doc-2"]}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
        side_effect=SideEffectClass.READ_ONLY,
    ),
    ToolDefinition(
        name="query_database",
        description="Run a named read-only database query.",
        input_schema={
            "type": "object",
            "properties": {
                "query_name": {"type": "string", "enum": ["ticket_count", "open_tickets"]}
            },
            "required": ["query_name"],
            "additionalProperties": False,
        },
        side_effect=SideEffectClass.READ_ONLY,
    ),
    ToolDefinition(
        name="create_ticket",
        description="Create a ticket exactly once for an idempotency key.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "idempotency_key": {"type": "string", "minLength": 1},
                "api_token": {"type": "string"},
            },
            "required": ["title", "idempotency_key"],
            "additionalProperties": False,
        },
        side_effect=SideEffectClass.MUTATING,
        sensitive_parameters=("api_token",),
    ),
    ToolDefinition(
        name="update_ticket",
        description="Update an existing ticket.",
        input_schema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "closed"]},
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["ticket_id", "status", "idempotency_key"],
            "additionalProperties": False,
        },
        side_effect=SideEffectClass.MUTATING,
    ),
    ToolDefinition(
        name="unstable_service",
        description="Read from a service intended for failure-injection tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        side_effect=SideEffectClass.READ_ONLY,
    ),
    ToolDefinition(
        name="unavailable_tool",
        description="A declared tool that deterministically reports unavailable.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        side_effect=SideEffectClass.READ_ONLY,
    ),
)


class MockMcpAdapter(McpAdapter):
    """Fully offline MCP lab with deterministic responses and failure injection."""

    def __init__(
        self,
        tools: Sequence[ToolDefinition] = DEFAULT_TOOLS,
        failures: Sequence[FailureInjection] = (),
        seed: int = 0,
    ) -> None:
        del seed  # Recorded by the experiment; behavior intentionally has no randomness.
        self._tools = tuple(tools)
        self._failures = {item.tool: item for item in failures}
        self._attempts: Dict[str, int] = {}
        self._side_effects: Dict[Tuple[str, str], Dict[str, Any]] = {}

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities("mock-mcp/1", "mock-mcp-lab", True, 1_000_000, True)

    def list_tools(self) -> Tuple[ToolDefinition, ...]:
        return self._tools

    def call(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        definitions = {tool.name: tool for tool in self._tools}
        tool = definitions.get(tool_name)
        if tool is None:
            return ToolCallResult(False, error=FailureKind.UNAVAILABLE, message="unknown tool")
        errors = sorted(
            Draft202012Validator(tool.input_schema).iter_errors(dict(arguments)), key=str
        )
        if errors:
            return ToolCallResult(False, error=FailureKind.PERMANENT, message=errors[0].message)
        forbidden = set(arguments) & set(tool.forbidden_parameters)
        if forbidden:
            return ToolCallResult(
                False, error=FailureKind.PERMANENT, message=f"forbidden parameters: {forbidden}"
            )
        attempt = self._attempts.get(tool_name, 0) + 1
        self._attempts[tool_name] = attempt
        injection = self._failures.get(tool_name)
        if injection and attempt <= injection.fail_attempts:
            if injection.kind == FailureKind.PARTIAL_RESULT:
                return ToolCallResult(
                    False,
                    value={"partial": True},
                    error=injection.kind,
                    message="injected partial result",
                    latency_ms=injection.latency_ms,
                    partial=True,
                )
            return ToolCallResult(
                False,
                error=injection.kind,
                message=f"injected {injection.kind.value}",
                latency_ms=injection.latency_ms,
            )
        if tool_name == "unavailable_tool":
            return ToolCallResult(False, error=FailureKind.UNAVAILABLE, message="tool unavailable")
        if tool.side_effect != SideEffectClass.READ_ONLY:
            key = str(arguments.get("idempotency_key", ""))
            identity = (tool_name, key)
            if identity in self._side_effects:
                return ToolCallResult(
                    False, error=FailureKind.PERMANENT, message="duplicate side effect blocked"
                )
            result = {"ticket_id": f"ticket-{len(self._side_effects) + 1}", "status": "ok"}
            self._side_effects[identity] = result
            return ToolCallResult(True, value=result)
        responses: Dict[str, Dict[str, Any]] = {
            "search_documents": {"document_ids": ["doc-1"]},
            "get_document": {"document_id": arguments.get("document_id"), "text": "MCP guide"},
            "query_database": {"rows": [{"count": 2}]},
            "unstable_service": {"status": "healthy"},
        }
        return ToolCallResult(True, value=responses.get(tool_name, {"status": "ok"}))


@dataclass(frozen=True)
class ProcessMcpConfig:
    executable: Path
    version: str
    sha256: str
    argv: Tuple[str, ...] = ()
    timeout_seconds: float = 5.0
    max_response_bytes: int = 1_000_000
    max_json_depth: int = 20
    max_json_fields: int = 1_000
    allowed_environment: Tuple[str, ...] = ("PATH", "LANG", "LC_ALL")
    tools: Tuple[ToolDefinition, ...] = ()


class ProcessMcpAdapter(McpAdapter):
    """Pinned, no-shell, one-request-per-process JSON adapter for real MCP integration."""

    def __init__(self, config: ProcessMcpConfig) -> None:
        self._config = config
        executable = config.executable.resolve(strict=True)
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        if digest != config.sha256:
            raise ValueError("process adapter executable SHA-256 mismatch")

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            f"process-json/{self._config.version}",
            f"{self._config.executable.name}@{self._config.version}",
            True,
            self._config.max_response_bytes,
            False,
        )

    def list_tools(self) -> Tuple[ToolDefinition, ...]:
        return self._config.tools

    def call(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        request = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": dict(arguments)},
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        env = {
            key: os.environ[key] for key in self._config.allowed_environment if key in os.environ
        }
        process = subprocess.Popen(  # noqa: S603 - executable is hash-pinned and shell is disabled.
            [str(self._config.executable.resolve()), *self._config.argv],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(request, timeout=self._config.timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return ToolCallResult(False, error=FailureKind.TIMEOUT, message="process timeout")
        if len(stdout) > self._config.max_response_bytes:
            return ToolCallResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="response too large"
            )
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ToolCallResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="invalid JSON"
            )
        if not _json_within_limits(
            payload, self._config.max_json_depth, self._config.max_json_fields
        ):
            return ToolCallResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="JSON limits exceeded"
            )
        if not isinstance(payload, dict):
            return ToolCallResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="response must be an object"
            )
        if process.returncode != 0 or "error" in payload:
            return ToolCallResult(False, error=FailureKind.PERMANENT, message="process MCP error")
        result = payload.get("result")
        if not isinstance(result, dict):
            return ToolCallResult(
                False, error=FailureKind.MALFORMED_RESPONSE, message="missing result"
            )
        return ToolCallResult(True, value=result)


def _json_within_limits(value: Any, max_depth: int, max_fields: int) -> bool:
    fields = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal fields
        if depth > max_depth:
            return False
        if isinstance(item, dict):
            fields += len(item)
            return fields <= max_fields and all(visit(child, depth + 1) for child in item.values())
        if isinstance(item, list):
            return all(visit(child, depth + 1) for child in item)
        return True

    return visit(value, 1)
