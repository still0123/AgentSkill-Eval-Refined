"""Smoke tests for the project CLI."""

import json
from pathlib import Path
from uuid import UUID

from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import DemoMode
from agentskill_eval_cli import __version__
from agentskill_eval_cli.main import app
from agentskill_eval_experiment import LocalExperimentStore

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]


def test_cli_help_lists_project_description() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Agent Skill evaluation" in result.stdout
    assert "schema" in result.stdout
    assert "storage" in result.stdout
    assert "dataset" in result.stdout
    assert "trace" in result.stdout
    assert "experiment" in result.stdout
    assert "benchmark" in result.stdout
    assert "optimize" in result.stdout
    assert "final" in result.stdout
    assert "real" in result.stdout
    assert "mcp" in result.stdout
    assert "memory-rag" in result.stdout
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
    workspace = tmp_path / "workspace"
    store = LocalExperimentStore(workspace)
    experiment_id = payload["experiment_id"]
    parsed_experiment_id = UUID(experiment_id)
    runs = store.list_runs(parsed_experiment_id)
    selected = runs[0]
    trace_result = runner.invoke(
        app, ["trace", "show", str(workspace), experiment_id, str(selected.id)]
    )
    assert trace_result.exit_code == 0, trace_result.stdout
    trace_payload = json.loads(trace_result.stdout)
    assert trace_payload["trace"]["events"]
    block_runs = [run for run in runs if run.pair_block_id == selected.pair_block_id]
    by_variant = {str(run.variant_id): run for run in block_runs}
    control = payload["control_variant_id"]
    treatment = payload["treatment_variant_id"]
    assert control in by_variant and treatment in by_variant
    compare_result = runner.invoke(
        app,
        [
            "trace",
            "compare",
            str(workspace),
            experiment_id,
            str(selected.pair_block_id),
            "--control",
            control,
            "--treatment",
            treatment,
        ],
    )
    assert compare_result.exit_code == 0, compare_result.stdout
    assert json.loads(compare_result.stdout)["pair_block_id"] == str(selected.pair_block_id)


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
        env={"COLUMNS": "240"},
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert "confirm-real-run" in result.output


def test_optimizer_simulation_requires_explicit_acknowledgement() -> None:
    result = runner.invoke(
        app,
        [
            "optimize",
            "search",
            str(ROOT / "examples/optimizer/python-review-search/search.example.yaml"),
        ],
        env={"COLUMNS": "240"},
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert "allow-simulation" in result.output


def test_final_evaluation_simulation_requires_explicit_acknowledgement() -> None:
    result = runner.invoke(
        app,
        [
            "final",
            "evaluate",
            str(ROOT / "examples/optimizer/python-review-search/final.example.yaml"),
        ],
        env={"COLUMNS": "240"},
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert "allow-simulation" in result.output


def test_real_smoke_requires_explicit_budget_options() -> None:
    result = runner.invoke(
        app,
        ["real", "smoke", str(ROOT / "README.md")],
        env={"COLUMNS": "240"},
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert "max-cost-microusd" in result.output
    second = runner.invoke(
        app,
        [
            "real",
            "smoke",
            str(ROOT / "README.md"),
            "--max-cost-microusd",
            "1000",
        ],
        env={"COLUMNS": "240"},
        terminal_width=240,
    )
    assert second.exit_code == 2
    assert "max-agent-runs" in second.output
