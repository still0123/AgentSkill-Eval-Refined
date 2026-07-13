"""End-to-end CLI coverage for the offline MCP evaluation lab."""

import json
from pathlib import Path

from typer.testing import CliRunner

from agentskill_eval_cli.main import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def test_mcp_cli_validates_runs_reports_and_traces(tmp_path: Path) -> None:
    dataset = ROOT / "examples/mcp/dataset.yaml"
    config = ROOT / "examples/mcp/lab-config.yaml"
    validated = runner.invoke(app, ["mcp", "validate", str(dataset)])
    assert validated.exit_code == 0, validated.stdout
    assert json.loads(validated.stdout)["case_count"] == 2

    denied = runner.invoke(
        app,
        ["mcp", "lab", "run", str(config), "--workspace", str(tmp_path)],
        env={"COLUMNS": "240"},
        terminal_width=240,
    )
    assert denied.exit_code == 2
    assert "allow-simulation" in denied.output

    executed = runner.invoke(
        app,
        [
            "mcp",
            "lab",
            "run",
            str(config),
            "--workspace",
            str(tmp_path),
            "--allow-simulation",
        ],
    )
    assert executed.exit_code == 0, executed.stdout
    payload = json.loads(executed.stdout)
    assert payload["simulated"] is True
    assert "not evidence" in payload["claim_limit"]
    report_json = Path(payload["report_json"])
    report_html = Path(payload["report_html"])
    assert report_json.is_file() and report_html.is_file()
    html_text = report_html.read_text(encoding="utf-8")
    assert "<script" not in html_text
    assert "SIMULATED" in html_text

    report_result = runner.invoke(app, ["mcp", "report", str(tmp_path), payload["experiment_id"]])
    assert report_result.exit_code == 0, report_result.stdout
    report = json.loads(report_result.stdout)
    assert report["paired_metrics"]["wins"] >= 1
    assert report["paired_metrics"]["invalid"] == 0
    run_id = report["runs"][0]["run"]["run_id"]
    trace_result = runner.invoke(app, ["mcp", "trace", str(tmp_path), run_id])
    assert trace_result.exit_code == 0, trace_result.stdout
    trace = json.loads(trace_result.stdout)
    assert [event["sequence"] for event in trace["events"]] == list(
        range(1, len(trace["events"]) + 1)
    )


def test_html_escapes_agent_controlled_content(tmp_path: Path) -> None:
    config = (ROOT / "examples/mcp/lab-config.yaml").read_text(encoding="utf-8")
    local_dataset = tmp_path / "dataset.yaml"
    local_dataset.write_text(
        (ROOT / "examples/mcp/dataset.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    local_config = tmp_path / "config.yaml"
    local_config.write_text(
        config.replace("dataset.yaml", str(local_dataset)).replace(
            "MCP guide found in doc-1.", "<img src=x onerror=alert(1)> MCP guide"
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "mcp",
            "lab",
            "run",
            str(local_config),
            "--workspace",
            str(tmp_path / "workspace"),
            "--allow-simulation",
        ],
    )
    assert result.exit_code == 0, result.stdout
    rendered = Path(json.loads(result.stdout)["report_html"]).read_text(encoding="utf-8")
    assert "<img src=x" not in rendered
