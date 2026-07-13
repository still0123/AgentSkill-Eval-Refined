from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agentskill_eval_runner_adapters import (
    RunnerRequest,
    RunnerStatus,
    SkillUpRunnerAdapter,
    discover_skill_up_binary,
)

ROOT = Path(__file__).resolve().parents[2]
BINARY = discover_skill_up_binary()


@pytest.mark.skipif(BINARY is None, reason="pinned skill-up binary is not installed")
def test_pinned_skill_up_custom_engine_contract(tmp_path: Path) -> None:
    assert BINARY is not None
    eval_dir = ROOT / "tests/fixtures/runner_eval"
    code = (
        "import json,sys; json.load(open(sys.argv[1])); "
        "print(json.dumps({'exit_code':0,'final_message':'custom-engine-handled',"
        "'turns':1,'input_tokens':7,'output_tokens':3}))"
    )
    request = RunnerRequest(
        execution_id="real-skill-up",
        case_id="golden-pass",
        variant="baseline",
        source_eval_dir=eval_dir,
        case_file=eval_dir / "cases/golden-pass.yaml",
        run_dir=tmp_path,
        engine={
            "name": "golden-local",
            "custom": {
                "transport": "local",
                "response_format": "session_result",
                "local": {
                    "command": sys.executable,
                    "args": ["-c", code, "${input_file}"],
                },
            },
        },
        environment={"type": "none"},
        timeout_seconds=30,
        max_turns=3,
    )
    adapter = SkillUpRunnerAdapter(BINARY)

    async def scenario() -> None:
        validation = await adapter.validate(request)
        assert validation.valid, validation.stderr
        result = await adapter.execute(request)
        assert result.status == RunnerStatus.PASS, result.stderr
        assert result.final_message == "custom-engine-handled"
        assert result.input_tokens == 7
        assert result.output_tokens == 3
        assert any(item.path == "result.json" for item in result.artifacts)

    asyncio.run(scenario())
