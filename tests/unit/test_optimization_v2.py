from pathlib import Path

import pytest

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
