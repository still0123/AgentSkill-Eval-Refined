from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from agentskill_eval_runner_adapters import (
    ExitReason,
    MockRunnerAdapter,
    ResultParseError,
    RunnerRequest,
    RunnerResult,
    RunnerStatus,
    SkillUpRunnerAdapter,
    compile_evaluation,
    parse_skill_up_result,
)
from agentskill_eval_runner_adapters.process import ProcessSupervisor

ROOT = Path(__file__).resolve().parents[2]


def request(tmp_path: Path, *, skill_path: Optional[Path] = None) -> RunnerRequest:
    eval_dir = ROOT / "tests/fixtures/runner_eval"
    return RunnerRequest(
        execution_id="execution-1",
        case_id="golden-pass",
        variant="treatment" if skill_path else "baseline",
        source_eval_dir=eval_dir,
        case_file=eval_dir / "cases/golden-pass.yaml",
        run_dir=tmp_path,
        engine={"name": "mock"},
        environment={"type": "none"},
        timeout_seconds=30,
        max_turns=3,
        skill_path=skill_path,
    )


def test_compile_baseline_is_explicit_and_anchored(tmp_path: Path) -> None:
    compiled = compile_evaluation(request(tmp_path))
    config = json.loads(compiled.eval_path.read_text(encoding="utf-8"))
    assert config["skills"] == []
    assert config["benchmark"] == {"enabled": False}
    assert config["cases"]["parallelism"] == 1
    assert config["cases"]["retry_policy"]["max_retries"] == 0
    assert config["cases"]["files"] == ["evals/cases/golden-pass.yaml"]
    assert (compiled.root / "SKILL.md").is_file()


def test_compile_treatment_copies_only_selected_skill(tmp_path: Path) -> None:
    skill = tmp_path / "source-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Selected\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    compiled = compile_evaluation(request(run_dir, skill_path=skill))
    config = json.loads(compiled.eval_path.read_text(encoding="utf-8"))
    assert config["skills"] == [{"path": "skills/selected"}]
    assert (compiled.root / "skills/selected/SKILL.md").read_text() == "# Selected\n"


def test_compiler_rejects_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "evals"
    (source / "cases").mkdir(parents=True)
    target = tmp_path / "outside.yaml"
    target.write_text("id: escaped\n", encoding="utf-8")
    (source / "cases/escaped.yaml").symlink_to(target)
    req = RunnerRequest(
        execution_id="e",
        case_id="escaped",
        variant="baseline",
        source_eval_dir=source,
        case_file=source / "cases/escaped.yaml",
        run_dir=tmp_path / "run",
        engine={"name": "mock"},
        environment={"type": "none"},
        timeout_seconds=10,
        max_turns=1,
    )
    with pytest.raises(ValueError, match="symlink"):
        compile_evaluation(req)


def test_agent_home_files_are_isolated_and_never_contain_secrets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    req = request(tmp_path / "run")
    req = RunnerRequest(
        **{
            **req.__dict__,
            "agent_home_files": {
                ".qwen/settings.json": {
                    "modelProviders": {
                        "openai": [{"envKey": "OPENAI_API_KEY", "baseUrl": "https://api.test"}]
                    }
                }
            },
            "secret_env": {"OPENAI_API_KEY": "never-persist-this-value"},
        }
    )
    SkillUpRunnerAdapter._materialize_agent_home_files(req, home)
    settings = home / ".qwen/settings.json"
    assert settings.is_file()
    assert settings.stat().st_mode & 0o777 == 0o600
    assert "never-persist-this-value" not in settings.read_text(encoding="utf-8")

    unsafe = RunnerRequest(
        **{
            **req.__dict__,
            "agent_home_files": {"../escaped.json": {"value": "safe"}},
        }
    )
    with pytest.raises(ValueError, match="unsafe Agent HOME"):
        SkillUpRunnerAdapter._materialize_agent_home_files(unsafe, home)

    leaked = RunnerRequest(
        **{
            **req.__dict__,
            "agent_home_files": {"settings.json": {"value": "never-persist-this-value"}},
        }
    )
    with pytest.raises(ValueError, match="contains a Secret"):
        SkillUpRunnerAdapter._materialize_agent_home_files(leaked, home)

    (home / "linked").symlink_to(tmp_path)
    linked = RunnerRequest(
        **{
            **req.__dict__,
            "agent_home_files": {"linked/escaped.json": {"value": "safe"}},
        }
    )
    with pytest.raises(ValueError, match="escapes isolated HOME|contains a symlink"):
        SkillUpRunnerAdapter._materialize_agent_home_files(linked, home)


def test_parser_preserves_unknown_fields_and_does_not_trust_exit_code(tmp_path: Path) -> None:
    fixture = ROOT / "runner_compatibility/skill-up/v0.5.0/fixtures/pass-result.json"
    result_path = tmp_path / "result.json"
    result_path.write_bytes(fixture.read_bytes())
    result = parse_skill_up_result(result_path, "e", "golden-pass", process_exit_code=1)
    assert result.status == RunnerStatus.PASS
    assert result.exit_reason == ExitReason.COMPLETED
    assert result.process_exit_code == 1
    assert result.raw_result["future_top_level"] == "preserved"
    assert result.raw_result["case_results"][0]["future_field"]["must_be_preserved"] is True
    artifact = next(item for item in result.artifacts if item.path == "result.json")
    assert artifact.sha256 == hashlib.sha256(result_path.read_bytes()).hexdigest()


def test_parser_requires_exact_case(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text('{"case_results": []}', encoding="utf-8")
    with pytest.raises(ResultParseError, match="exactly one"):
        parse_skill_up_result(path, "e", "missing", 0)


def test_mock_runner_emits_events_and_supports_cancel(tmp_path: Path) -> None:
    async def scenario() -> None:
        events = []

        async def sink(event: object) -> None:
            events.append(event)

        adapter = MockRunnerAdapter(delay_seconds=10)
        task = asyncio.create_task(adapter.execute(request(tmp_path), sink))
        await asyncio.sleep(0)
        assert await adapter.cancel("execution-1") is True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(events) == 1

    asyncio.run(scenario())


def test_process_supervisor_marks_external_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor()
        task = asyncio.create_task(
            supervisor.run(
                "slow-process",
                ["/bin/sh", "-c", "sleep 10"],
                tmp_path,
                {"PATH": "/usr/bin:/bin"},
                30,
            )
        )
        await asyncio.sleep(0.05)
        assert await supervisor.cancel("slow-process") is True
        outcome = await task
        assert outcome.cancelled is True
        assert outcome.timed_out is False

    asyncio.run(scenario())


def test_process_supervisor_terminates_nested_process_groups(tmp_path: Path) -> None:
    async def scenario() -> None:
        child_pid = tmp_path / "child.pid"
        code = (
            "import pathlib,subprocess,time;"
            f"p=subprocess.Popen(['/bin/sleep','10'],start_new_session=True);"
            f"pathlib.Path({str(child_pid)!r}).write_text(str(p.pid));"
            "time.sleep(10)"
        )
        supervisor = ProcessSupervisor()
        task = asyncio.create_task(
            supervisor.run(
                "nested-process",
                [sys.executable, "-c", code],
                tmp_path,
                {"PATH": "/usr/bin:/bin"},
                30,
            )
        )
        for _ in range(50):
            if child_pid.exists():
                break
            await asyncio.sleep(0.02)
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        assert await supervisor.cancel("nested-process") is True
        outcome = await task
        assert outcome.cancelled is True
        await asyncio.sleep(0.1)
        probe = subprocess.run(("ps", "-p", str(pid)), check=False, capture_output=True)
        assert probe.returncode != 0

    asyncio.run(scenario())


def test_qwen_usage_aggregates_main_and_subagent_tokens(tmp_path: Path) -> None:
    req = request(tmp_path)
    req = RunnerRequest(**{**req.__dict__, "engine": {"name": "qwen_code"}})
    usage = tmp_path / "runner-home/.qwen/usage/token-usage-2026-07.jsonl"
    usage.parent.mkdir(parents=True)
    usage.write_text(
        '\n'.join(
            (
                json.dumps({"inputTokens": 100, "outputTokens": 10, "cachedTokens": 80}),
                json.dumps({"inputTokens": 50, "outputTokens": 5, "cachedTokens": 40}),
            )
        ),
        encoding="utf-8",
    )
    result = RunnerResult(
        execution_id=req.execution_id,
        case_id=req.case_id,
        status=RunnerStatus.PASS,
        exit_reason=ExitReason.COMPLETED,
        process_exit_code=0,
    )
    accounted = SkillUpRunnerAdapter._with_qwen_usage(req, result)
    assert accounted.input_tokens == 150
    assert accounted.output_tokens == 15
    assert accounted.cached_input_tokens == 120
    assert accounted.raw_result["usage_accounting"]["includes_subagents"] is True
