"""End-to-end deterministic Memory/RAG CLI and report tests."""

import json
from pathlib import Path

from typer.testing import CliRunner

from agentskill_eval_cli.main import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def test_memory_rag_cli_validate_run_report_trace_and_replay(tmp_path: Path) -> None:
    dataset = ROOT / "examples/memory-rag/dataset.yaml"
    config = ROOT / "examples/memory-rag/lab-config.yaml"
    validated = runner.invoke(app, ["memory-rag", "validate", str(dataset)])
    assert validated.exit_code == 0, validated.stdout
    assert json.loads(validated.stdout)["case_count"] == 4

    denied = runner.invoke(
        app,
        ["memory-rag", "lab", "run", str(config), "--workspace", str(tmp_path)],
        terminal_width=240,
    )
    assert denied.exit_code == 2
    assert "allow-simulation" in denied.output

    command = [
        "memory-rag",
        "lab",
        "run",
        str(config),
        "--workspace",
        str(tmp_path),
        "--allow-simulation",
    ]
    first = runner.invoke(app, command)
    assert first.exit_code == 0, first.stdout
    payload = json.loads(first.stdout)
    report_json = Path(payload["report_json"])
    report_html = Path(payload["report_html"])
    first_json = report_json.read_bytes()
    first_html = report_html.read_bytes()
    second = runner.invoke(app, command)
    assert second.exit_code == 0, second.stdout
    assert report_json.read_bytes() == first_json
    assert report_html.read_bytes() == first_html
    assert b"<script" not in first_html
    assert b"SIMULATED" in first_html
    assert b"super-secret" not in first_json + first_html
    assert b"ignore previous" not in first_json + first_html
    assert b"The current capacity is 42" not in first_json + first_html

    report_result = runner.invoke(
        app, ["memory-rag", "report", str(tmp_path), payload["experiment_id"]]
    )
    assert report_result.exit_code == 0, report_result.stdout
    report = json.loads(report_result.stdout)
    assert {item["pair_type"] for item in report["paired_metrics"]} == {
        "no_rag_vs_with_rag",
        "no_memory_vs_with_memory",
        "clean_context_vs_noisy_context",
        "clean_memory_vs_poisoned_memory",
    }
    run_id = report["runs"][0]["run"]["run_id"]
    trace_result = runner.invoke(app, ["memory-rag", "trace", str(tmp_path), run_id])
    assert trace_result.exit_code == 0, trace_result.stdout
    trace = json.loads(trace_result.stdout)
    assert trace["simulated"] is True


def test_memory_rag_html_escapes_dataset_content(tmp_path: Path) -> None:
    dataset_text = (ROOT / "examples/memory-rag/dataset.yaml").read_text(encoding="utf-8")
    local_dataset = tmp_path / "dataset.yaml"
    local_dataset.write_text(
        dataset_text.replace("independence_group: retrieval", "independence_group: '<img src=x>'"),
        encoding="utf-8",
    )
    config_text = (ROOT / "examples/memory-rag/lab-config.yaml").read_text(encoding="utf-8")
    local_config = tmp_path / "config.yaml"
    local_config.write_text(config_text, encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "memory-rag",
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
    assert "<img src=x>" not in rendered
    assert "&lt;img src=x&gt;" in rendered
