from pathlib import Path

import pytest

from agentskill_eval_contracts import SearchCaseResult
from agentskill_eval_skill_optimizer import OptimizationV2Error, OptimizationV2ScreeningRunner
from agentskill_eval_skill_optimizer.optimization_v2 import OptimizationV2Spec

PROJECT = Path(__file__).resolve().parents[2]


def _portable_spec() -> OptimizationV2Spec:
    return OptimizationV2Spec(
        schema_version="ase/optimization-evaluation-v2/v1alpha1",
        name="portable-v2-preflight-fixture",
        base_skill_path=PROJECT / "examples/skills/python-bug-fix-v1",
        proposal_directory=PROJECT / "examples/optimizer/failure-guided",
        failure_bundle_path=(
            PROJECT / "examples/optimizer/failure-guided/qwen3-cachetools-failure-bundle-v4.yaml"
        ),
        real_agent_config_path=PROJECT / "examples/real-agent-evidence/observed-agent.example.yaml",
        validation_search_path=PROJECT / "examples/datasets",
        case_ids=("portable-case-a", "portable-case-b"),
        target_model="portable-model",
    )


def test_screening_refuses_before_any_real_call_when_evidence_is_misaligned(
    tmp_path: Path,
) -> None:
    spec = _portable_spec()

    with pytest.raises(OptimizationV2Error):
        OptimizationV2ScreeningRunner(tmp_path).run(
            spec,
            confirm_real_run=True,
            max_cost_microusd=100_000,
            max_agent_runs=12,
        )

    assert not (tmp_path / "runtime").exists()


def test_screening_spec_allows_four_generated_candidates() -> None:
    spec = _portable_spec()

    expanded = spec.model_copy(update={"max_candidates": 4, "max_agent_runs": 20})

    assert expanded.max_candidates == 4
    assert expanded.max_agent_runs == 20


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("DeepSeek HTTP 402 Insufficient Balance", "insufficient_balance"),
        ("DeepSeek HTTP 429 rate limited", "rate_limited"),
        ("diagnosis receipt sha256=c4024024a9d1", "agent_invalid"),
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
