"""Rule-based MCP controller and grader tests."""

from pathlib import Path

from agentskill_eval_mcp_lab import FailureInjection, FailureKind, McpDataset, MockMcpAdapter
from agentskill_eval_mcp_lab.contracts import McpCase
from agentskill_eval_mcp_lab.evaluation import (
    AgentPlan,
    CompositeMcpGrader,
    McpEvaluationController,
    ToolAction,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET = McpDataset.load(ROOT / "examples/mcp/dataset.yaml")


def test_missing_parameter_wrong_tool_and_sequence_are_scored() -> None:
    case = DATASET.cases[0]
    plan = AgentPlan(
        actions=(
            ToolAction(tool="get_document", arguments={}),
            ToolAction(tool="search_documents", arguments={"query": "wrong"}),
        ),
        final_response="MCP guide",
    )
    run = McpEvaluationController().run(
        case, MockMcpAdapter(case.available_tools), plan, "without_guidance"
    )
    score = CompositeMcpGrader().grade(case, run)
    assert score.parameter_accuracy < 1
    assert score.sequence_score == 0
    assert score.outcome == "fail"


def test_transient_failure_recovers_after_bounded_retry() -> None:
    case = DATASET.cases[1]
    adapter = MockMcpAdapter(
        case.available_tools,
        (FailureInjection("unstable_service", FailureKind.TRANSIENT, 1),),
    )
    plan = AgentPlan(
        actions=(ToolAction(tool="unstable_service", max_retries=1),),
        final_response="healthy",
    )
    run = McpEvaluationController().run(case, adapter, plan, "with_guidance")
    score = CompositeMcpGrader().grade(case, run)
    assert score.recovery_score == 1
    assert score.retry_count == 1
    assert score.total_tool_calls == 2
    assert score.outcome == "pass"


def test_permanent_failure_is_not_retried_and_retry_budget_blocks_loop() -> None:
    case = DATASET.cases[1]
    adapter = MockMcpAdapter(
        case.available_tools,
        (FailureInjection("unstable_service", FailureKind.PERMANENT, 10),),
    )
    plan = AgentPlan(actions=(ToolAction(tool="unstable_service", max_retries=10),))
    run = McpEvaluationController().run(case, adapter, plan, "with_guidance")
    score = CompositeMcpGrader().grade(case, run)
    assert score.retry_count == 0
    assert score.total_tool_calls == 1


def test_timeout_and_rate_limit_obey_max_tool_calls() -> None:
    case = DATASET.cases[1]
    for failure in (FailureKind.TIMEOUT, FailureKind.RATE_LIMIT):
        adapter = MockMcpAdapter(
            case.available_tools,
            (FailureInjection("unstable_service", failure, 10),),
        )
        plan = AgentPlan(actions=(ToolAction(tool="unstable_service", max_retries=10),))
        run = McpEvaluationController().run(case, adapter, plan, "with_guidance")
        score = CompositeMcpGrader().grade(case, run)
        assert score.total_tool_calls == case.max_tool_calls
        assert score.retry_count == case.max_tool_calls - 1
        assert score.outcome == "invalid"


def test_unavailable_tool_is_invalid() -> None:
    case = DATASET.cases[0]
    plan = AgentPlan(actions=(ToolAction(tool="invented_tool"),))
    run = McpEvaluationController().run(
        case, MockMcpAdapter(case.available_tools), plan, "without_guidance"
    )
    assert CompositeMcpGrader().grade(case, run).outcome == "invalid"


def test_no_tool_needed_scores_selection_without_calling_a_tool() -> None:
    raw = DATASET.cases[0].model_dump(mode="python")
    raw.update(
        expected_tools=(),
        required_parameters=(),
        parameter_constraints=(),
        allowed_sequences=(),
        forbidden_sequences=(),
        oracle={"final_status": "success"},
    )
    case = McpCase.model_validate(raw)
    run = McpEvaluationController().run(
        case, MockMcpAdapter(case.available_tools), AgentPlan(actions=()), "with_guidance"
    )
    score = CompositeMcpGrader().grade(case, run)
    assert score.selection_accuracy == 1
    assert score.total_tool_calls == 0
    assert score.outcome == "pass"


def test_unconfirmed_mutation_is_rejected_before_adapter_call() -> None:
    raw = DATASET.cases[0].model_dump(mode="python")
    raw["forbidden_tools"] = tuple(
        name for name in raw["forbidden_tools"] if name != "create_ticket"
    )
    raw["side_effect_policy"] = {
        "allow_mutating": True,
        "require_confirmation": True,
        "confirmation_token": "approved",
    }
    case = type(DATASET.cases[0]).model_validate(raw)
    plan = AgentPlan(
        actions=(
            ToolAction(
                tool="create_ticket",
                arguments={"title": "bug", "idempotency_key": "key"},
                max_retries=3,
            ),
        )
    )
    adapter = MockMcpAdapter(case.available_tools)
    run = McpEvaluationController().run(case, adapter, plan, "without_guidance")
    score = CompositeMcpGrader().grade(case, run)
    assert score.safety_score == 0
    assert score.retry_count == 0
    assert adapter.call("create_ticket", {"title": "bug", "idempotency_key": "key"}).ok is True
