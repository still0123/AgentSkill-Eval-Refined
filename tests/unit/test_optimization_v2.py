from pathlib import Path

import pytest

from agentskill_eval_contracts import SearchCaseResult
from agentskill_eval_skill_optimizer import OptimizationV2Error, OptimizationV2ScreeningRunner
from agentskill_eval_skill_optimizer.optimization_v2 import OptimizationV2Spec

PROJECT = Path(__file__).resolve().parents[2]


def test_screening_refuses_before_any_real_call_when_evidence_is_misaligned(
    tmp_path: Path,
) -> None:
    spec = OptimizationV2Spec.load(
        PROJECT / "examples/optimizer/failure-guided/optimization-v2.example.yaml"
    )

    with pytest.raises(OptimizationV2Error, match="preflight is insufficient"):
        OptimizationV2ScreeningRunner(tmp_path).run(
            spec,
            confirm_real_run=True,
            max_cost_microusd=100_000,
            max_agent_runs=12,
        )

    assert not (tmp_path / "runtime").exists()


def test_screening_spec_allows_four_generated_candidates() -> None:
    spec = OptimizationV2Spec.load(
        PROJECT / "examples/optimizer/failure-guided/optimization-v2.example.yaml"
    )

    expanded = spec.model_copy(update={"max_candidates": 4, "max_agent_runs": 20})

    assert expanded.max_candidates == 4
    assert expanded.max_agent_runs == 20


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("DeepSeek HTTP 402 Insufficient Balance", "insufficient_balance"),
        ("DeepSeek HTTP 429 rate limited", "rate_limited"),
        ("provider request timed out", "provider_timeout"),
        ("real optimizer cost budget exhausted", "budget_exhausted"),
        ("unexpected runner failure", "agent_invalid"),
    ],
)
def test_screening_classifies_terminal_provider_and_budget_errors(
    tmp_path: Path, message: str, expected: str
) -> None:
    assert OptimizationV2ScreeningRunner(tmp_path)._classify_error_text(message) == expected


def test_task_failure_is_not_an_agent_invalid() -> None:
    result = SearchCaseResult(
        case_id="case",
        passed=False,
        score=0,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        outcome="fail",
    )

    assert OptimizationV2ScreeningRunner(Path("."))._result_error_type(result) == "task_failed"
