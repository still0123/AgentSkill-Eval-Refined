"""Smoke tests for the project CLI."""

import json
from pathlib import Path

from click import Command, Group, Option
from typer.main import get_command
from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import DemoMode
from agentskill_eval_cli import __version__
from agentskill_eval_cli.main import app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]


def _option(command_path: tuple[str, ...], name: str) -> Option:
    command: Command = get_command(app)
    for part in command_path:
        assert isinstance(command, Group)
        command = command.commands[part]
    matches = [parameter for parameter in command.params if parameter.name == name]
    assert len(matches) == 1
    assert isinstance(matches[0], Option)
    return matches[0]


def test_cli_help_lists_project_description() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Agent Skill evaluation" in result.stdout
    assert "schema" in result.stdout
    assert "dataset" in result.stdout
    assert "benchmark" in result.stdout
    assert "optimize" in result.stdout
    assert "final" in result.stdout
    assert "real" in result.stdout
    assert "skill" in result.stdout
    assert "demo" in result.stdout


def test_version_command_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_version_option_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_dataset_validate_reports_frozen_demo_identity() -> None:
    result = runner.invoke(
        app,
        ["dataset", "validate", str(ROOT / "examples/datasets/python-review-demo")],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["name"] == "python-review-demo"
    assert payload["case_count"] == 12
    assert payload["demo_only"] is True
    assert len(payload["independence_groups"]) == 6


def test_demo_run_command_executes_service_free_mock_loop(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "demo",
            "run",
            "--workspace",
            str(tmp_path / "workspace"),
            "--bootstrap-resamples",
            "10",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["simulated"] is True
    assert payload["logical_runs"] == 72
    assert payload["completed_runs"] == 72
    assert Path(payload["html_report"]).is_file()


def test_demo_real_mode_requires_explicit_cost_confirmation(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "demo",
            "run",
            "--workspace",
            str(tmp_path / "workspace"),
            "--mode",
            DemoMode.SKILL_UP.value,
        ],
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert _option(("demo", "run"), "confirm_real_run").default is False


def test_optimizer_simulation_requires_explicit_acknowledgement() -> None:
    result = runner.invoke(
        app,
        [
            "optimize",
            "search",
            str(ROOT / "examples/optimizer/python-review-search/search.example.yaml"),
        ],
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert _option(("optimize", "search"), "allow_simulation").default is False


def test_final_evaluation_simulation_requires_explicit_acknowledgement() -> None:
    result = runner.invoke(
        app,
        [
            "final",
            "evaluate",
            str(ROOT / "examples/optimizer/python-review-search/final.example.yaml"),
        ],
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert _option(("final", "evaluate"), "allow_simulation").default is False


def test_real_smoke_requires_explicit_budget_options() -> None:
    result = runner.invoke(
        app,
        ["real", "smoke", str(ROOT / "README.md")],
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert _option(("real", "smoke"), "max_cost_microusd").required is True
    second = runner.invoke(
        app,
        [
            "real",
            "smoke",
            str(ROOT / "README.md"),
            "--max-cost-microusd",
            "1000",
        ],
        terminal_width=240,
    )
    assert second.exit_code == 2
    assert _option(("real", "smoke"), "max_agent_runs").required is True
    assert _option(("real", "smoke"), "confirm_real_run").default is False
