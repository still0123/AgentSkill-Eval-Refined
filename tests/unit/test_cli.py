"""Smoke tests for the project CLI."""

from typer.testing import CliRunner

from agentskill_eval_cli import __version__
from agentskill_eval_cli.main import app

runner = CliRunner()


def test_cli_help_lists_project_description() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Agent Skill evaluation" in result.stdout
    assert "version" in result.stdout


def test_version_command_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_version_option_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
