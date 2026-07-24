"""Bounded step-loop controllers that reuse the deterministic lab graders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, cast
from uuid import NAMESPACE_URL, uuid5

from agentskill_eval_contracts import stable_sha256
from agentskill_eval_mcp_lab import McpCase
from agentskill_eval_mcp_lab.adapters import McpAdapter, ToolCallResult
from agentskill_eval_mcp_lab.contracts import (
    FailureKind as McpFailureKind,
)
from agentskill_eval_mcp_lab.contracts import (
    McpEventKind,
    McpTrace,
    McpTraceEvent,
    SideEffectClass,
    ToolDefinition,
    redact_arguments,
)
from agentskill_eval_mcp_lab.evaluation import RunOutcome as McpRunOutcome
from agentskill_eval_memory_rag_lab import MemoryRagCase
from agentskill_eval_memory_rag_lab.adapters import (
    MemoryAdapter,
    RetrievedDocument,
    RetrieverAdapter,
    contains_poison_pattern,
)
from agentskill_eval_memory_rag_lab.contracts import (
    MemoryRagEventKind,
    MemoryRagTrace,
    MemoryRagTraceEvent,
    secret_summary,
    stable_hash,
)
from agentskill_eval_memory_rag_lab.evaluation import (
    Claim,
    GenerationOutput,
    MemoryObservation,
)
from agentskill_eval_memory_rag_lab.evaluation import (
    RunOutcome as MemoryRagRunOutcome,
)
from agentskill_eval_scenarios.contracts import SkillUnderTest
from agentskill_eval_scenarios.interactive import (
    InteractionHistoryEvent,
    InteractiveAgentAction,
    InteractiveRunEvidence,
    InteractiveTraceEvent,
)
from agentskill_eval_scenarios.process_agent import ProcessScenarioAgentClient


def _evidence_event(
    events: List[InteractiveTraceEvent],
    step: int,
    kind: str,
    *,
    action: Optional[InteractiveAgentAction] = None,
    target: Optional[str] = None,
    status: Optional[str] = None,
    payload: Optional[object] = None,
) -> None:
    events.append(
        InteractiveTraceEvent(
            sequence=len(events) + 1,
            step=step,
            kind=cast(Any, kind),
            action_kind=action.kind if action else None,
            target=target,
            status=status,
            payload_sha256=(
                stable_sha256(
                    payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
                )
                if payload is not None
                else None
            ),
        )
    )


@dataclass
class _InteractiveLifecycle:
    """Shared auditable lifecycle around scenario-specific action execution."""

    scenario: Literal["mcp_tool", "memory_rag"]
    case_id: str
    variant: str
    client: ProcessScenarioAgentClient
    skill: Optional[SkillUnderTest]
    audit: List[InteractiveTraceEvent] = field(default_factory=list)
    history: List[InteractionHistoryEvent] = field(default_factory=list)
    token_count: int = 0
    cost_usd: float = 0.0
    termination: Literal["final", "step_limit", "error"] = "step_limit"

    def start(self) -> None:
        _evidence_event(self.audit, 0, "agent.session.started", status="started")
        if self.skill:
            _evidence_event(
                self.audit,
                0,
                "skill.activated",
                status="verified",
                payload=self.skill.expected_sha256,
            )

    def next_action(
        self, step: int, case_payload: Mapping[str, object]
    ) -> InteractiveAgentAction:
        _evidence_event(self.audit, step, "agent.decision.requested")
        action = self.client.next_action(
            self.scenario,
            self.case_id,
            self.variant,
            case_payload,
            self.history,
            self.skill,
            step,
        )
        self.token_count += action.token_count
        self.cost_usd += action.cost_usd
        return action

    def record_proposed_action(
        self, step: int, action: InteractiveAgentAction, target: Optional[str]
    ) -> None:
        _evidence_event(
            self.audit,
            step,
            "agent.action.proposed",
            action=action,
            target=target,
            payload=action,
        )

    def finish(self, step: int, action: InteractiveAgentAction) -> None:
        _evidence_event(self.audit, step, "agent.final", action=action, status="completed")
        self.termination = "final"

    def record_observation(
        self,
        step: int,
        action: InteractiveAgentAction,
        target: Optional[str],
        observation: Dict[str, Any],
    ) -> None:
        self.history.append(
            InteractionHistoryEvent(step=step, action=action, observation=observation)
        )
        _evidence_event(
            self.audit,
            step,
            "environment.observation",
            action=action,
            target=target,
            status=str(observation["status"]),
            payload=observation,
        )

    def mark_step_limit(self) -> None:
        _evidence_event(
            self.audit, self.client.spec.max_steps, "agent.step_limit", status="exhausted"
        )

    def evidence(self) -> InteractiveRunEvidence:
        return InteractiveRunEvidence(
            scenario=self.scenario,
            case_id=self.case_id,
            variant=self.variant,
            skill_present=self.skill is not None,
            skill_sha256=self.skill.expected_sha256 if self.skill else None,
            max_steps=self.client.spec.max_steps,
            completed=self.termination == "final",
            termination=self.termination,
            events=tuple(self.audit),
        )


class InteractiveMcpController:
    RETRYABLE = {
        McpFailureKind.TIMEOUT,
        McpFailureKind.TRANSIENT,
        McpFailureKind.RATE_LIMIT,
    }

    def run(
        self,
        case: McpCase,
        adapter: McpAdapter,
        client: ProcessScenarioAgentClient,
        variant: Literal["without_guidance", "with_guidance"],
        skill: Optional[SkillUnderTest],
    ) -> Tuple[McpRunOutcome, InteractiveRunEvidence]:
        run_id = uuid5(NAMESPACE_URL, f"agentskill-eval:mcp:{case.case_id}:{variant}")
        attempt_id = uuid5(run_id, "attempt-1")
        trace_events: List[McpTraceEvent] = []
        lifecycle = _InteractiveLifecycle("mcp_tool", case.case_id, variant, client, skill)
        definitions = {tool.name: tool for tool in case.available_tools}
        available = {tool.name for tool in adapter.list_tools()}
        calls = 0
        hard_failure = False
        tool_budget_exhausted = False
        final_response = ""
        last_failure: Optional[Tuple[str, McpFailureKind]] = None

        def record(
            kind: McpEventKind,
            *,
            tool: Optional[ToolDefinition] = None,
            name: Optional[str] = None,
            arguments: Mapping[str, Any] = {},
            result: Optional[ToolCallResult] = None,
            retry: int = 0,
            status: Optional[str] = None,
        ) -> None:
            sequence = len(trace_events) + 1
            trace_events.append(
                McpTraceEvent(
                    attempt_id=attempt_id,
                    sequence=sequence,
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc)
                    + timedelta(milliseconds=sequence),
                    kind=kind,
                    server_identity=adapter.capabilities.server_identity,
                    tool_name=name or (tool.name if tool else None),
                    arguments_summary=redact_arguments(
                        arguments, tool.sensitive_parameters if tool else ()
                    ),
                    response_status=status
                    or ("ok" if result and result.ok else result.message if result else None),
                    latency_ms=result.latency_ms if result else 0,
                    error_category=result.error if result else None,
                    retry_number=retry,
                    side_effect=tool.side_effect if tool else None,
                )
            )

        lifecycle.start()
        record(McpEventKind.SERVER_CONNECTED, status="connected")
        record(McpEventKind.TOOLS_LISTED, status=",".join(sorted(available)))
        case_payload: Dict[str, object] = {
            "task": case.task,
            "available_tools": [item.model_dump(mode="json") for item in case.available_tools],
            "max_tool_calls": case.max_tool_calls,
            "side_effect_policy": case.side_effect_policy.model_dump(mode="json"),
        }
        for step in range(1, client.spec.max_steps + 1):
            action = lifecycle.next_action(step, case_payload)
            target = action.tool
            lifecycle.record_proposed_action(step, action, target)
            if action.kind == "final":
                final_response = action.answer or ""
                lifecycle.finish(step, action)
                break
            if action.kind != "tool_call":
                hard_failure = True
                observation: Dict[str, Any] = {
                    "ok": False,
                    "status": "rejected",
                    "error": "action_not_supported",
                }
                _evidence_event(
                    lifecycle.audit,
                    step,
                    "environment.action.rejected",
                    action=action,
                    status="unsupported",
                )
            elif calls >= case.max_tool_calls:
                hard_failure = True
                tool_budget_exhausted = True
                observation = {"ok": False, "status": "rejected", "error": "tool_budget"}
                _evidence_event(
                    lifecycle.audit,
                    step,
                    "environment.action.rejected",
                    action=action,
                    status="tool_budget",
                )
            else:
                action_accepted = False
                tool = definitions.get(action.tool or "")
                record(
                    McpEventKind.TOOL_REQUESTED,
                    tool=tool,
                    name=action.tool,
                    arguments=action.arguments,
                )
                calls += 1
                if tool is None or action.tool not in available:
                    result = ToolCallResult(
                        False, error=McpFailureKind.UNAVAILABLE, message="unavailable tool"
                    )
                    record(McpEventKind.TOOL_FAILED, name=action.tool, result=result)
                    hard_failure = True
                elif not self._authorized(case, tool, action, record):
                    result = ToolCallResult(
                        False, error=McpFailureKind.PERMANENT, message="not authorized"
                    )
                    hard_failure = True
                else:
                    action_accepted = True
                    if last_failure is not None and last_failure[0] == tool.name:
                        record(
                            McpEventKind.TOOL_RETRIED,
                            tool=tool,
                            arguments=action.arguments,
                            retry=1,
                            status="agent retry",
                        )
                    result = adapter.call(tool.name, action.arguments)
                    kind = McpEventKind.TOOL_SUCCEEDED if result.ok else McpEventKind.TOOL_FAILED
                    if result.error == McpFailureKind.TIMEOUT:
                        kind = McpEventKind.TOOL_TIMEOUT
                    record(kind, tool=tool, arguments=action.arguments, result=result)
                    if not result.ok and result.error not in self.RETRYABLE:
                        hard_failure = True
                last_failure = (
                    (action.tool or "", result.error)
                    if not result.ok and result.error in self.RETRYABLE
                    else None
                )
                observation = {
                    "ok": result.ok,
                    "status": "ok" if result.ok else "failed",
                    "value": result.value,
                    "error": result.error.value if result.error else None,
                    "message": result.message,
                }
                _evidence_event(
                    lifecycle.audit,
                    step,
                    (
                        "environment.action.accepted"
                        if action_accepted
                        else "environment.action.rejected"
                    ),
                    action=action,
                    target=action.tool,
                    status=observation["status"],
                )
            lifecycle.record_observation(step, action, target, observation)
        else:
            lifecycle.mark_step_limit()
        trace = McpTrace(
            run_id=run_id, case_id=case.case_id, simulated=True, events=tuple(trace_events)
        )
        outcome = McpRunOutcome(
            run_id=run_id,
            variant=variant,
            case_id=case.case_id,
            trace=trace,
            final_response=final_response,
            token_count=lifecycle.token_count,
            cost_usd=lifecycle.cost_usd,
            completed=(
                not hard_failure and last_failure is None and lifecycle.termination == "final"
            ),
            budget_exhausted=lifecycle.termination == "step_limit" or tool_budget_exhausted,
            simulated=True,
        )
        return outcome, lifecycle.evidence()

    @staticmethod
    def _authorized(
        case: McpCase,
        tool: ToolDefinition,
        action: InteractiveAgentAction,
        record: Any,
    ) -> bool:
        if tool.side_effect == SideEffectClass.READ_ONLY:
            return True
        record(McpEventKind.SIDE_EFFECT_REQUESTED, tool=tool, arguments=action.arguments)
        allowed = case.side_effect_policy.allow_mutating
        if tool.side_effect == SideEffectClass.DESTRUCTIVE:
            allowed = case.side_effect_policy.allow_destructive
        confirmed = (
            not case.side_effect_policy.require_confirmation
            or action.confirmation_token == case.side_effect_policy.confirmation_token
        )
        if not allowed or not confirmed:
            record(McpEventKind.SIDE_EFFECT_REJECTED, tool=tool, status="not authorized")
            return False
        record(McpEventKind.SIDE_EFFECT_CONFIRMED, tool=tool, status="confirmed")
        return True


class InteractiveMemoryRagController:
    def run(
        self,
        case: MemoryRagCase,
        retriever: RetrieverAdapter,
        memory: MemoryAdapter,
        client: ProcessScenarioAgentClient,
        pair_type: str,
        variant: Literal["control", "treatment"],
        skill: Optional[SkillUnderTest],
    ) -> Tuple[MemoryRagRunOutcome, InteractiveRunEvidence]:
        run_id = uuid5(NAMESPACE_URL, f"memory-rag:{case.case_id}:{variant}")
        attempt_id = uuid5(run_id, "attempt-1")
        trace_events: List[MemoryRagTraceEvent] = []
        lifecycle = _InteractiveLifecycle("memory_rag", case.case_id, variant, client, skill)
        retrieved: Tuple[RetrievedDocument, ...] = ()
        context: Tuple[str, ...] = ()
        observations: List[MemoryObservation] = []
        generation = GenerationOutput()

        def record(
            kind: MemoryRagEventKind,
            identity: str,
            status: str,
            *,
            session_id: Optional[str] = None,
            key: Optional[str] = None,
            value: Optional[str] = None,
            document_ids: Tuple[str, ...] = (),
            latency_ms: float = 0,
            cost_usd: float = 0,
            error: Optional[str] = None,
        ) -> None:
            sequence = len(trace_events) + 1
            trace_events.append(
                MemoryRagTraceEvent(
                    attempt_id=attempt_id,
                    sequence=sequence,
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc)
                    + timedelta(milliseconds=sequence),
                    kind=kind,
                    adapter_identity=identity,
                    session_id_hash=stable_hash(session_id) if session_id else None,
                    document_ids=document_ids,
                    key_hash=stable_hash(key) if key else None,
                    value_summary=secret_summary(value) if value is not None else None,
                    status=status,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    error_category=error,
                )
            )

        lifecycle.start()
        case_payload: Dict[str, object] = {
            "pair_type": pair_type,
            "task": case.task,
            "kind": case.kind,
            "query": case.query,
            "k": case.k,
            "documents": [
                {"document_id": item.document_id, "text": item.text} for item in case.documents
            ],
            "memory_policy": {
                "forbidden_keys": list(case.forbidden_memory_keys),
                "sensitive_keys": list(case.sensitive_memory_keys),
            },
        }
        for step in range(1, client.spec.max_steps + 1):
            action = lifecycle.next_action(step, case_payload)
            target = action.operation or ("retriever" if action.kind == "retrieve" else None)
            lifecycle.record_proposed_action(step, action, target)
            if action.kind == "final":
                claims = tuple(Claim.model_validate(item) for item in action.claims)
                generation = GenerationOutput(
                    answer=action.answer or "", citations=action.citations, claims=claims
                )
                lifecycle.finish(step, action)
                break
            if action.kind == "retrieve":
                record(
                    MemoryRagEventKind.RETRIEVAL_QUERY,
                    retriever.capabilities.identity,
                    "requested",
                )
                retrieval_result = retriever.retrieve(
                    action.query or "", action.k or 1, action.mode or "ranked"
                )
                if retrieval_result.ok:
                    retrieved = retrieval_result.documents
                    context = tuple(item.document_id for item in retrieved)
                    if retrieval_result.filtered_document_ids:
                        record(
                            MemoryRagEventKind.RETRIEVAL_FILTERED,
                            retriever.capabilities.identity,
                            "filtered",
                            document_ids=retrieval_result.filtered_document_ids,
                        )
                    record(
                        MemoryRagEventKind.RETRIEVAL_RESULT,
                        retriever.capabilities.identity,
                        "ok",
                        document_ids=context,
                        latency_ms=retrieval_result.latency_ms,
                        cost_usd=retrieval_result.cost_usd,
                    )
                    observation: Dict[str, Any] = {
                        "ok": True,
                        "status": "ok",
                        "documents": [
                            {
                                "document_id": item.document_id,
                                "text": next(
                                    doc.text
                                    for doc in case.documents
                                    if doc.document_id == item.document_id
                                ),
                            }
                            for item in retrieved
                        ],
                    }
                else:
                    record(
                        MemoryRagEventKind.RETRIEVAL_FAILED,
                        retriever.capabilities.identity,
                        retrieval_result.message or "failed",
                        latency_ms=retrieval_result.latency_ms,
                        error=(retrieval_result.error.value if retrieval_result.error else None),
                    )
                    observation = {
                        "ok": False,
                        "status": "failed",
                        "error": (retrieval_result.error.value if retrieval_result.error else None),
                        "message": retrieval_result.message,
                    }
            elif action.kind == "memory":
                memory_result = memory.apply(
                    action.operation or "read",
                    action.session_id or "",
                    action.key or "",
                    action.value,
                    action.ttl_steps,
                )
                poison = memory_result.poisoned or contains_poison_pattern(action.value)
                if poison:
                    record(
                        MemoryRagEventKind.MEMORY_POISON_DETECTED,
                        memory.capabilities.identity,
                        "detected"
                        if memory_result.status == "rejected"
                        else "detected-but-accepted",
                        session_id=action.session_id,
                        key=action.key,
                        value=action.value,
                    )
                kind = _memory_kind(
                    action.operation or "read", memory_result.status, memory_result.ok
                )
                record(
                    kind,
                    memory.capabilities.identity,
                    memory_result.status,
                    session_id=action.session_id,
                    key=action.key,
                    value=(
                        memory_result.value if memory_result.value is not None else action.value
                    ),
                    latency_ms=memory_result.latency_ms,
                    error=memory_result.error.value if memory_result.error else None,
                )
                observations.append(
                    MemoryObservation(
                        operation=action.operation or "read",
                        session_id_hash=stable_hash(action.session_id or ""),
                        key_hash=stable_hash(action.key or ""),
                        status=memory_result.status,
                        value_sha256=(
                            stable_hash(memory_result.value)
                            if memory_result.value is not None
                            else None
                        ),
                        poisoned=poison,
                        error_category=(memory_result.error.value if memory_result.error else None),
                    )
                )
                observation = {
                    "ok": memory_result.ok,
                    "status": memory_result.status,
                    "value": memory_result.value,
                    "error": memory_result.error.value if memory_result.error else None,
                    "message": memory_result.message,
                }
            else:
                observation = {
                    "ok": False,
                    "status": "rejected",
                    "error": "action_not_supported",
                }
                _evidence_event(
                    lifecycle.audit,
                    step,
                    "environment.action.rejected",
                    action=action,
                    status="unsupported",
                )
            if action.kind in {"retrieve", "memory"}:
                _evidence_event(
                    lifecycle.audit,
                    step,
                    "environment.action.accepted",
                    action=action,
                    target=target,
                    status=str(observation["status"]),
                )
            lifecycle.record_observation(step, action, target, observation)
        else:
            lifecycle.mark_step_limit()
        if case.kind == "retrieval_generation":
            record(
                MemoryRagEventKind.CONTEXT_ASSEMBLED,
                retriever.capabilities.identity,
                "assembled",
                document_ids=context,
            )
        trace = MemoryRagTrace(
            run_id=run_id, case_id=case.case_id, simulated=True, events=tuple(trace_events)
        )
        outcome = MemoryRagRunOutcome(
            run_id=run_id,
            variant=variant,
            case_id=case.case_id,
            trace=trace,
            retrieved_documents=retrieved,
            context_document_ids=context,
            generation=generation,
            memory_observations=tuple(observations),
            token_count=lifecycle.token_count,
            cost_usd=lifecycle.cost_usd,
            simulated=True,
        )
        return outcome, lifecycle.evidence()


def _memory_kind(operation: str, status: str, ok: bool) -> MemoryRagEventKind:
    if status == "rejected" or not ok:
        return MemoryRagEventKind.MEMORY_REJECTED
    if status == "expired" or operation == "expire":
        return MemoryRagEventKind.MEMORY_EXPIRED
    if operation == "read":
        return MemoryRagEventKind.MEMORY_READ
    if operation in {"update", "overwrite"}:
        return MemoryRagEventKind.MEMORY_UPDATE
    if operation in {"delete", "forget"}:
        return MemoryRagEventKind.MEMORY_DELETE
    return MemoryRagEventKind.MEMORY_WRITE
