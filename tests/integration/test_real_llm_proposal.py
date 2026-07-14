"""Proposal-only real LLM generation through a local OpenAI-compatible fake API."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest
import yaml
from click import unstyle
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_skill_optimizer import RealLLMProposalSpec

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/optimizer/failure-guided/deepseek-proposal.example.yaml"
runner = CliRunner()


def _compact_cli_output(value: str) -> str:
    return "".join(character for character in unstyle(value) if not character.isspace())


class _ProposalAPIHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, bytes]] = []

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers["Content-Length"])
        body = self.rfile.read(size)
        self.__class__.requests.append(
            (self.path, self.headers.get("Authorization", ""), body)
        )
        proposals = [
            {
                "id": "normalize-boundary-values",
                "failure_label": "TASK_UNDERSTANDING",
                "hypothesis": "Canonical boundary checks reduce interpretation mismatches.",
                "instruction": "Normalize producer and consumer values before comparing them.",
                "risks": ["May duplicate normalization already guaranteed by a caller."],
            },
            {
                "id": "inspect-exception-cleanup",
                "failure_label": "PLANNING",
                "hypothesis": "Exceptional-path review reduces incomplete resource analysis.",
                "instruction": "Verify cleanup on every exception path before reporting a leak.",
                "risks": ["May increase inspection latency."],
            },
            {
                "id": "require-runtime-evidence",
                "failure_label": "VERIFICATION",
                "hypothesis": (
                    "A <script>alert(1)</script> evidence gate reduces unsupported edits."
                ),
                "instruction": "Require reachable runtime evidence before reporting a defect.",
                "risks": ["May reduce recall when execution evidence is unavailable."],
            },
            {
                "id": "account-retry-budget",
                "failure_label": "TOOL_RECOVERY",
                "hypothesis": "Explicit retry accounting prevents recovery budget mistakes.",
                "instruction": "Derive attempt counts from the configured retry budget.",
                "risks": ["May add one bookkeeping step before recovery."],
            },
        ]
        content = json.dumps(
            {
                "schema_version": "ase/process-hypothesis-response/v1alpha1",
                "hypotheses": proposals,
            }
        )
        response = json.dumps(
            {
                "id": "fake-proposal-response",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": 500,
                    "prompt_cache_hit_tokens": 100,
                    "completion_tokens": 300,
                    "total_tokens": 800,
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _fake_proposal_api() -> Iterator[str]:
    _ProposalAPIHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProposalAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _config(tmp_path: Path, base_url: str) -> Path:
    spec = RealLLMProposalSpec.load(EXAMPLE)
    generator = spec.generator.model_copy(update={"base_url": base_url})
    config = tmp_path / "proposal.yaml"
    config.write_text(
        yaml.safe_dump(
            spec.model_copy(update={"generator": generator}).model_dump(mode="json"),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config


def test_proposal_cli_is_budgeted_audited_idempotent_and_proposal_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-proposal-secret")
    with _fake_proposal_api() as base_url:
        config = _config(tmp_path, base_url)
        preflight = runner.invoke(app, ["optimize", "proposal", "preflight", str(config)])
        assert preflight.exit_code == 0, preflight.output
        preflight_payload = json.loads(preflight.stdout)
        assert preflight_payload["planned_calls"] == 1
        assert preflight_payload["candidate_count"] == 4
        assert preflight_payload["search_will_execute"] is False
        assert preflight_payload["locked_test_will_execute"] is False
        assert _ProposalAPIHandler.requests == []

        denied = runner.invoke(
            app,
            [
                "optimize",
                "proposal",
                "run",
                str(config),
                "--workspace",
                str(tmp_path / "denied"),
            ],
        )
        assert denied.exit_code != 0
        assert "--confirm-real-run" in _compact_cli_output(denied.output)
        assert _ProposalAPIHandler.requests == []

        no_budget = runner.invoke(
            app,
            [
                "optimize",
                "proposal",
                "run",
                str(config),
                "--workspace",
                str(tmp_path / "no-budget"),
                "--confirm-real-run",
            ],
        )
        assert no_budget.exit_code != 0
        assert "--max-cost-microusd/--max-calls" in _compact_cli_output(no_budget.output)
        assert _ProposalAPIHandler.requests == []

        workspace = tmp_path / "workspace"
        command = [
            "optimize",
            "proposal",
            "run",
            str(config),
            "--workspace",
            str(workspace),
            "--confirm-real-run",
            "--max-cost-microusd",
            "50000",
            "--max-calls",
            "1",
        ]
        allowed = runner.invoke(app, command)
        assert allowed.exit_code == 0, allowed.output
        summary = json.loads(allowed.stdout.strip().splitlines()[-1])
        assert summary["proposal_count"] == 4
        assert summary["calls_consumed"] == 1
        assert summary["search_executed"] is False
        assert summary["locked_test_accessed"] is False
        assert len(_ProposalAPIHandler.requests) == 1

        replay = runner.invoke(app, command)
        assert replay.exit_code == 0, replay.output
        replay_summary = json.loads(replay.stdout.strip().splitlines()[-1])
        assert replay_summary["calls_consumed"] == 0
        assert len(_ProposalAPIHandler.requests) == 1

    job_dir = Path(summary["manifest"]).parent
    verified = runner.invoke(app, ["optimize", "proposal", "verify", str(job_dir)])
    assert verified.exit_code == 0, verified.output
    inspected = runner.invoke(app, ["optimize", "proposal", "inspect", str(job_dir)])
    assert inspected.exit_code == 0, inspected.output
    manifest = json.loads(inspected.stdout)
    assert manifest["provider"] == "deepseek"
    assert manifest["real_run_confirmed"] is True
    assert manifest["input_evidence_class"] == "simulated_fixture"
    assert len(manifest["prompt_sha256"]) == 64
    assert len(manifest["output_schema_sha256"]) == 64
    assert len(manifest["request_sha256"]) == 64

    request_body = _ProposalAPIHandler.requests[0][2].decode("utf-8")
    assert "validation_search" not in request_body
    assert "regression_dev" not in request_body
    assert "locked_test" not in request_body
    assert "The Agent compared values" not in request_body
    assert "fake-proposal-secret" not in request_body
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in job_dir.iterdir()
        if path.is_file()
    )
    assert "fake-proposal-secret" not in persisted
    assert "The Agent compared values" not in persisted
    html = (job_dir / "proposal-report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    report = job_dir / "proposal-report.json"
    report.write_text(report.read_text(encoding="utf-8") + " ", encoding="utf-8")
    tampered = runner.invoke(app, ["optimize", "proposal", "verify", str(job_dir)])
    assert tampered.exit_code != 0
