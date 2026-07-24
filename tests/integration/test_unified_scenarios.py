"""Cross-scenario CLI coverage for one evaluation entrypoint and result envelope."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentskill_eval_cli.main import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.mark.parametrize(
    ("spec_name", "scenario", "case_count"),
    [
        ("software-engineering.yaml", "software_engineering", 12),
        ("mcp-tool.yaml", "mcp_tool", 2),
        ("memory-rag.yaml", "memory_rag", 2),
    ],
)
def test_unified_cli_validates_runs_and_reports(
    tmp_path: Path, spec_name: str, scenario: str, case_count: int
) -> None:
    spec = ROOT / "examples/unified" / spec_name
    validated = runner.invoke(app, ["scenario", "validate", str(spec)])
    assert validated.exit_code == 0, validated.stdout
    plan = json.loads(validated.stdout)
    assert plan["scenario"] == scenario
    assert plan["case_count"] == case_count
    assert plan["comparison"] == "skill_ab"
    assert len(plan["plan_sha256"]) == 64
    assert plan["variants"][0]["skill_sha256"] is None
    assert len(plan["variants"][1]["skill_sha256"]) == 64
    if scenario in {"mcp_tool", "memory_rag"}:
        assert "agent_decision" not in plan["trace_capabilities"]
        assert "agent_observation_loop" not in plan["trace_capabilities"]

    denied = runner.invoke(
        app, ["scenario", "run", str(spec), "--workspace", str(tmp_path)]
    )
    assert denied.exit_code == 2
    assert "allow_simulation" in denied.stdout

    command = [
        "scenario",
        "run",
        str(spec),
        "--workspace",
        str(tmp_path),
        "--allow-simulation",
    ]
    executed = runner.invoke(app, command)
    assert executed.exit_code == 0, executed.stdout
    summary = json.loads(executed.stdout)
    assert summary["scenario"] == scenario
    assert summary["simulated"] is True
    assert summary["evidence_class"] == "simulated_controller"

    report_path = Path(summary["report_json"])
    html_path = Path(summary["report_html"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["plan"]["scenario"] == scenario
    assert report["primary_metrics"]["invalid"] == 0
    assert len(report["artifacts"]) == 2
    assert report_path.with_suffix(".json.sha256").is_file()
    rendered = html_path.read_text(encoding="utf-8")
    assert "<script" not in rendered
    assert "Claim limit" in rendered

    replayed = runner.invoke(app, command)
    assert replayed.exit_code == 0, replayed.stdout
    reported = runner.invoke(
        app,
        ["scenario", "report", str(tmp_path), summary["experiment_id"]],
    )
    assert reported.exit_code == 0, reported.stdout
    assert json.loads(reported.stdout)["plan"] == report["plan"]
