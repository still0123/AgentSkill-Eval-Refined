from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from agentskill_eval_contracts import FailureLabel
from agentskill_eval_skill_optimizer import (
    DeepSeekGeneratorAuthorization,
    DeepSeekGeneratorError,
    DeepSeekHypothesisGenerator,
    HypothesisGeneratorSpec,
)


def _response(label: str = "VERIFICATION") -> bytes:
    hypotheses = [
        {
            "id": f"verify-runtime-invariant-{index}",
            "failure_label": label,
            "hypothesis": f"Runtime invariant number {index} prevents unsupported edits.",
            "instruction": f"Verify runtime invariant number {index} before changing code.",
            "risks": ["May add one targeted verification step."],
        }
        for index in range(1, 4)
    ]
    return json.dumps(
        {
            "id": "fake-deepseek-response",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "schema_version": ("ase/process-hypothesis-response/v1alpha1"),
                                "hypotheses": hypotheses,
                            }
                        ),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 20,
                "prompt_cache_miss_tokens": 80,
                "completion_tokens": 50,
                "total_tokens": 150,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
    ).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    response_bytes = _response()
    requests: list[tuple[str, str, bytes]] = []

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers["Content-Length"])
        body = self.rfile.read(size)
        self.__class__.requests.append((self.path, self.headers.get("Authorization", ""), body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.__class__.response_bytes)))
        self.end_headers()
        self.wfile.write(self.__class__.response_bytes)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _fake_api(response: bytes) -> Iterator[str]:
    _Handler.response_bytes = response
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _spec(base_url: str) -> HypothesisGeneratorSpec:
    return HypothesisGeneratorSpec(
        type="deepseek",
        name="deepseek-skill-proposal",
        version="prompt-v1",
        max_hypotheses=3,
        base_url=base_url,
        model="deepseek-v4-pro",
        secret_env_name="OPENAI_API_KEY",
        timeout_seconds=5,
    )


def _request() -> dict[str, object]:
    return {
        "schema_version": "ase/process-hypothesis-request/v1alpha1",
        "source_split": "train",
        "base_skill": {"sha256": "a" * 64, "content": "# Skill\nVerify fixes."},
        "eligible_failures": [
            {
                "label": "VERIFICATION",
                "rule_id": "observed-runtime-failure",
                "confidence": 0.9,
            }
        ],
        "max_hypotheses": 3,
        "output_contract": "structured_hypotheses_only_no_case_answers_no_hidden_reasoning",
    }


def test_deepseek_generator_calls_fake_api_and_records_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-api-key")
    authorization = DeepSeekGeneratorAuthorization(True, max_calls=1, max_cost_microusd=50_000)
    with _fake_api(_response()) as base_url:
        generator = DeepSeekHypothesisGenerator(_spec(base_url), authorization)
        result = generator.generate(_request(), (FailureLabel.VERIFICATION,))

    assert len(result.proposals) == 3
    assert result.evidence.provider == "deepseek"
    assert result.evidence.model == "deepseek-v4-pro"
    assert result.evidence.input_tokens == 100
    assert result.evidence.cached_input_tokens == 20
    assert result.evidence.output_tokens == 50
    assert result.evidence.reasoning_tokens == 0
    assert result.evidence.cost_microusd == 79
    assert result.evidence.raw_request_stored is False
    assert result.evidence.raw_response_stored is False
    assert result.evidence.secret_value_stored is False
    assert authorization.calls_consumed == 1
    assert authorization.observed_or_reserved_cost_microusd == 79
    assert len(_Handler.requests) == 1
    path, authorization_header, body = _Handler.requests[0]
    assert path == "/chat/completions"
    assert authorization_header == "Bearer fake-api-key"
    payload = json.loads(body)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert "validation_search" not in body.decode("utf-8")
    assert "locked_test" not in body.decode("utf-8")
    assert b"fake-api-key" not in body


def test_deepseek_generator_budget_gate_prevents_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-api-key")
    authorization = DeepSeekGeneratorAuthorization(False, max_calls=1, max_cost_microusd=50_000)
    with _fake_api(_response()) as base_url:
        generator = DeepSeekHypothesisGenerator(_spec(base_url), authorization)
        with pytest.raises(DeepSeekGeneratorError, match="requires confirmation"):
            generator.generate(_request(), (FailureLabel.VERIFICATION,))
    assert _Handler.requests == []


def test_deepseek_generator_rejects_invalid_or_ineligible_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-api-key")
    with _fake_api(_response("PLANNING")) as base_url:
        generator = DeepSeekHypothesisGenerator(
            _spec(base_url),
            DeepSeekGeneratorAuthorization(True, max_calls=1, max_cost_microusd=50_000),
        )
        with pytest.raises(DeepSeekGeneratorError, match="ineligible failure label"):
            generator.generate(_request(), (FailureLabel.VERIFICATION,))

    invalid_api = json.dumps(
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode()
    with _fake_api(invalid_api) as base_url:
        generator = DeepSeekHypothesisGenerator(
            _spec(base_url),
            DeepSeekGeneratorAuthorization(True, max_calls=1, max_cost_microusd=50_000),
        )
        with pytest.raises(DeepSeekGeneratorError, match="structured JSON"):
            generator.generate(_request(), (FailureLabel.VERIFICATION,))
