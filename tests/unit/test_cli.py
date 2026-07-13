"""Smoke tests for the project CLI."""

import json
from pathlib import Path

from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import DemoMode
from agentskill_eval_cli import __version__
from agentskill_eval_cli.main import app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]


def test_cli_help_lists_project_description() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Agent Skill evaluation" in result.stdout
    assert "schema" in result.stdout
    assert "storage" in result.stdout
    assert "dataset" in result.stdout
    assert "version" in result.stdout


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
    )

    assert result.exit_code == 2
    assert "confirm-real-run" in result.stdout
