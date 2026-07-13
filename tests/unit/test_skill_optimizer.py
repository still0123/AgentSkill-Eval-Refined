from __future__ import annotations

import json
import sys
from pathlib import Path

from agentskill_eval_contracts import SearchEvaluationStage
from agentskill_eval_skill_optimizer import (
    EvaluatorSpec,
    ProcessEvaluator,
    SearchCase,
)


def test_process_evaluator_requires_exact_case_order(tmp_path: Path) -> None:
    script = tmp_path / "evaluator.py"
    script.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "results = []\n"
        "for case_id in request['case_ids']:\n"
        "    results.append({'case_id': case_id, 'passed': True, 'score': 1.0, "
        "'input_tokens': 10, 'output_tokens': 2, 'latency_ms': 5, "
        "'cost_microusd': 1})\n"
        "json.dump({'results': results}, sys.stdout)\n",
        encoding="utf-8",
    )
    skill = tmp_path / "SKILL.md"
    skill.write_text("# Test Skill\n", encoding="utf-8")
    dataset = tmp_path / "validation-search.yaml"
    dataset.write_text("test", encoding="utf-8")
    cases = (SearchCase(id="case-one", required_terms=("test",)),)
    evaluator = ProcessEvaluator(
        EvaluatorSpec(
            type="process",
            command=(sys.executable, str(script)),
            version="test-v1",
            simulated=True,
        )
    )

    result = evaluator.evaluate(
        skill,
        dataset,
        "0" * 64,
        cases,
        SearchEvaluationStage.FULL,
        timeout_seconds=10,
    )

    assert result.simulated is True
    assert result.pass_rate == 1.0
    assert result.total_tokens == 12
    assert result.total_cost_microusd == 1
    assert json.loads(json.dumps(result.model_dump(mode="json")))["case_ids"] == ["case-one"]
