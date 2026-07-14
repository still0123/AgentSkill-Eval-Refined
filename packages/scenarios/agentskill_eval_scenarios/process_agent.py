"""Hash-pinned local Process Agent that produces scenario-specific execution plans."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

from pydantic import Field

from agentskill_eval_contracts import FrozenModel, stable_sha256
from agentskill_eval_mcp_lab import AgentPlan as McpAgentPlan
from agentskill_eval_mcp_lab import McpCase
from agentskill_eval_memory_rag_lab import AgentPlan as MemoryRagAgentPlan
from agentskill_eval_memory_rag_lab import MemoryRagCase
from agentskill_eval_scenarios.contracts import ProcessScenarioAgentSpec, SkillUnderTest


class ProcessAgentError(RuntimeError):
    """Raised when a Process Agent violates its pinned execution contract."""


class AgentDecisionEvidence(FrozenModel):
    schema_version: Literal["ase/process-agent-decision/v1alpha1"] = (
        "ase/process-agent-decision/v1alpha1"
    )
    scenario: Literal["mcp_tool", "memory_rag"]
    case_id: str = Field(min_length=1)
    variant: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_present: bool
    skill_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: float = Field(ge=0)
    exit_code: int
    hidden_reasoning_stored: Literal[False] = False


class ProcessScenarioAgentClient:
    """One decision per isolated, no-shell child process."""

    def __init__(self, spec: ProcessScenarioAgentSpec) -> None:
        self.spec = spec
        expanded = spec.executable.expanduser()
        if expanded.is_symlink():
            raise ValueError("Process Scenario Agent executable must not be a symlink")
        self.executable = expanded.resolve(strict=True)
        if not self.executable.is_file():
            raise ValueError("Process Scenario Agent executable must be a regular file")
        digest = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        if digest != spec.expected_sha256:
            raise ValueError("Process Scenario Agent executable SHA-256 mismatch")
        self._verify_version()
        self._evidence: list[AgentDecisionEvidence] = []

    @property
    def evidence(self) -> Tuple[AgentDecisionEvidence, ...]:
        return tuple(self._evidence)

    def decide_mcp(
        self,
        case: McpCase,
        variant: Literal["without_guidance", "with_guidance"],
        skill: Optional[SkillUnderTest],
    ) -> McpAgentPlan:
        request = self._request(
            "mcp_tool",
            case.case_id,
            variant,
            {
                "task": case.task,
                "available_tools": [
                    tool.model_dump(mode="json") for tool in case.available_tools
                ],
                "max_tool_calls": case.max_tool_calls,
                "side_effect_policy": case.side_effect_policy.model_dump(mode="json"),
            },
            skill,
        )
        payload = self._execute(request, "mcp_tool", case.case_id, variant, skill)
        try:
            return McpAgentPlan.model_validate(payload)
        except ValueError as exc:
            raise ProcessAgentError(f"invalid MCP Agent plan: {exc}") from exc

    def decide_memory_rag(
        self,
        case: MemoryRagCase,
        pair_type: str,
        variant: Literal["control", "treatment"],
        skill: Optional[SkillUnderTest],
    ) -> MemoryRagAgentPlan:
        request = self._request(
            "memory_rag",
            case.case_id,
            variant,
            {
                "pair_type": pair_type,
                "task": case.task,
                "kind": case.kind,
                "query": case.query,
                "k": case.k,
                "documents": [
                    {"document_id": item.document_id, "text": item.text}
                    for item in case.documents
                ],
                "memory_policy": {
                    "forbidden_keys": list(case.forbidden_memory_keys),
                    "sensitive_keys": list(case.sensitive_memory_keys),
                },
            },
            skill,
        )
        payload = self._execute(request, "memory_rag", case.case_id, variant, skill)
        try:
            return MemoryRagAgentPlan.model_validate(payload)
        except ValueError as exc:
            raise ProcessAgentError(f"invalid Memory/RAG Agent plan: {exc}") from exc

    def _request(
        self,
        scenario: str,
        case_id: str,
        variant: str,
        case: Mapping[str, object],
        skill: Optional[SkillUnderTest],
    ) -> Dict[str, object]:
        skill_payload: Optional[Dict[str, str]] = None
        if skill is not None:
            root = skill.verify()
            skill_payload = {
                "name": skill.name,
                "version": skill.version,
                "sha256": skill.expected_sha256,
                "content": (root / "SKILL.md").read_text(encoding="utf-8"),
            }
        return {
            "schema_version": "ase/process-agent-request/v1alpha1",
            "scenario": scenario,
            "case_id": case_id,
            "variant": variant,
            "case": dict(case),
            "skill": skill_payload,
            "output_contract": "agent_plan_only_no_hidden_reasoning",
        }

    def _execute(
        self,
        request: Dict[str, object],
        scenario: Literal["mcp_tool", "memory_rag"],
        case_id: str,
        variant: str,
        skill: Optional[SkillUnderTest],
    ) -> Dict[str, object]:
        request_bytes = json.dumps(
            request, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        started = time.monotonic()
        process = subprocess.Popen(
            [str(self.executable), *self.spec.argv],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._environment(),
            start_new_session=True,
        )
        try:
            stdout, _stderr = process.communicate(
                request_bytes + b"\n", timeout=self.spec.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ProcessAgentError("Process Scenario Agent timed out") from exc
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise
        duration_ms = (time.monotonic() - started) * 1000
        if process.returncode != 0:
            raise ProcessAgentError(
                f"Process Scenario Agent exited with code {process.returncode}"
            )
        if len(stdout) > self.spec.max_response_bytes:
            raise ProcessAgentError("Process Scenario Agent response is too large")
        try:
            response = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProcessAgentError("Process Scenario Agent returned invalid JSON") from exc
        if not isinstance(response, dict) or set(response) != {"schema_version", "plan"}:
            raise ProcessAgentError(
                "Process Scenario Agent response must contain only schema_version and plan"
            )
        if response["schema_version"] != "ase/process-agent-response/v1alpha1":
            raise ProcessAgentError("unsupported Process Scenario Agent response version")
        plan = response["plan"]
        if not isinstance(plan, dict) or not _json_within_limits(plan, 20, 1_000):
            raise ProcessAgentError("Process Scenario Agent plan exceeds JSON limits")
        response_sha256 = stable_sha256(response)
        self._evidence.append(
            AgentDecisionEvidence(
                scenario=scenario,
                case_id=case_id,
                variant=variant,
                agent_name=self.spec.name,
                agent_version=self.spec.version,
                executable_sha256=self.spec.expected_sha256,
                skill_present=skill is not None,
                skill_sha256=skill.expected_sha256 if skill else None,
                request_sha256=hashlib.sha256(request_bytes).hexdigest(),
                response_sha256=response_sha256,
                duration_ms=duration_ms,
                exit_code=process.returncode,
            )
        )
        return plan

    def _verify_version(self) -> None:
        try:
            process = subprocess.Popen(
                [str(self.executable), *self.spec.argv, *self.spec.version_args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._environment(),
                start_new_session=True,
            )
            stdout, _stderr = process.communicate(timeout=self.spec.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError("Process Scenario Agent version check timed out") from exc
        except OSError as exc:
            raise ValueError(f"Process Scenario Agent version check failed: {exc}") from exc
        if (
            process.returncode != 0
            or stdout.decode("utf-8", errors="replace").strip()
            != self.spec.expected_version_output
        ):
            raise ValueError("Process Scenario Agent version mismatch")

    def _environment(self) -> Dict[str, str]:
        return {
            name: os.environ[name]
            for name in self.spec.allowed_environment
            if name in os.environ
        }


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
