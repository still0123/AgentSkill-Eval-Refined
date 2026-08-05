from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "examples/real-agent-evidence/qwen_openai_process_agent.py"


def _module():
    spec = importlib.util.spec_from_file_location("qwen_process_agent_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replace_in_file_edits_one_focused_region_without_truncating(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "large.py"
    source.write_text(
        "header\n" + ("filler = 1\n" * 2000) + "target = 1\nfooter\n",
        encoding="utf-8",
    )

    result = module._run_tool(
        tmp_path,
        "replace_in_file",
        {"path": "large.py", "old": "target = 1", "new": "target = 2"},
    )

    assert result.startswith("OK:")
    content = source.read_text(encoding="utf-8")
    assert content.startswith("header\n")
    assert content.endswith("footer\n")
    assert content.count("target = 2") == 1


def test_write_file_refuses_existing_source_to_prevent_truncation(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "existing.py"
    source.write_text("original\n", encoding="utf-8")

    result = module._run_tool(tmp_path, "write_file", {"path": "existing.py", "content": "bad"})

    assert "replace_in_file" in result
    assert source.read_text(encoding="utf-8") == "original\n"


def test_read_file_honors_one_based_line_range(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "lines.py"
    source.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")

    result = module._run_tool(
        tmp_path,
        "read_file",
        {"path": "lines.py", "offset": 3, "limit": 2},
    )

    assert result == "three\nfour\n"


def test_replace_in_file_rejects_ambiguous_match(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "ambiguous.py"
    source.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    result = module._run_tool(
        tmp_path,
        "replace_in_file",
        {"path": "ambiguous.py", "old": "value = 1", "new": "value = 2"},
    )

    assert "multiple locations" in result
    assert source.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_run_processes_structured_tools_and_preserves_large_file(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    source = tmp_path / "module.py"
    source.write_text("prefix\n" + ("padding = 1\n" * 2000) + "value = 1\n", encoding="utf-8")
    responses = iter(
        [
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "read-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"module.py"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "replace-1",
                                    "type": "function",
                                    "function": {
                                        "name": "replace_in_file",
                                        "arguments": (
                                            '{"path":"module.py","old":"value = 1",'
                                            '"new":"value = 2"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Applied the focused fix.",
                        }
                    }
                ],
            },
        ]
    )
    monkeypatch.setattr(module, "_json_request", lambda *args, **kwargs: next(responses))

    result = module.run(
        {"workspace": str(tmp_path), "case_id": "generic-case", "max_turns": 4},
        "http://fake",
        "fake-model",
    )

    assert result["final_message"] == "Applied the focused fix."
    assert source.read_text(encoding="utf-8").endswith("value = 2\n")
    assert len(result["transcript"]) >= 3
    assert result["turns"] == 3
    assert result["tool_calls"] == 2


def test_test_generation_run_uses_task_contract_and_hash_bound_skill(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    production = tmp_path / "module.py"
    production.write_text("value = 1\n", encoding="utf-8")
    skill_content = "# Test Generation\nWrite the requested regression test before finishing.\n"
    skill_sha256 = hashlib.sha256(skill_content.encode("utf-8")).hexdigest()
    home = tmp_path / "home"
    context = home / ".agentskill-eval" / "selected-skill.json"
    context.parent.mkdir(parents=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "ase/process-agent-skill/v1alpha1",
                "content": skill_content,
                "sha256": skill_sha256,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    payloads = []
    responses = iter(
        [
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "write-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "agent_regression_test.py",
                                                "content": (
                                                    "raise AssertionError('bug reproduced')\n"
                                                ),
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "test-1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_command",
                                        "arguments": (
                                            '{"command":"python3 agent_regression_test.py"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Created agent_regression_test.py; it fails on the buggy checkout."
                            ),
                        }
                    }
                ],
            },
        ]
    )

    def fake_request(_url, payload, _api_key):
        payloads.append(payload)
        return next(responses)

    monkeypatch.setattr(module, "_json_request", fake_request)
    result = module.run(
        {
            "workspace": str(tmp_path),
            "case_id": "testgen-generic-regression",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Write agent_regression_test.py. Do not modify production code."
                    ),
                }
            ],
            "max_turns": 4,
        },
        "http://fake",
        "fake-model",
    )

    system_prompt = payloads[0]["messages"][0]["content"]
    assert "Create agent_regression_test.py" in system_prompt
    assert "do not modify production code" in system_prompt.lower()
    assert "implement the requested bug fix" not in system_prompt
    assert skill_content in system_prompt
    assert result["skill_context_sha256"] == skill_sha256
    assert (tmp_path / "agent_regression_test.py").is_file()
    assert production.read_text(encoding="utf-8") == "value = 1\n"


def test_test_generation_empty_completion_retries_until_artifact_exists(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    calls = 0
    responses = iter(
        [
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                "choices": [{"message": {"role": "assistant", "content": ""}}],
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "write-1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": (
                                            '{"path":"agent_regression_test.py",'
                                            '"content":"raise AssertionError()\\n"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Created the required regression test.",
                        }
                    }
                ],
            },
        ]
    )

    def fake_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr(module, "_json_request", fake_request)
    result = module.run(
        {
            "workspace": str(tmp_path),
            "case_id": "testgen-empty-completion",
            "messages": [{"role": "user", "content": "Write agent_regression_test.py."}],
            "max_turns": 4,
        },
        "http://fake",
        "fake-model",
    )

    assert calls == 3
    assert (tmp_path / "agent_regression_test.py").is_file()
    assert result["final_message"] == "Created the required regression test."


def test_run_honors_explicit_model_turn_limit_and_returns_cache_usage(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    calls = 0

    def fake_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 3,
                "prompt_cache_hit_tokens": 11,
            },
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "read-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"module.py"}',
                                },
                            }
                        ],
                    }
                }
            ],
        }

    monkeypatch.setattr(module, "_json_request", fake_request)
    result = module.run(
        {"workspace": str(tmp_path), "case_id": "generic-case", "max_turns": 8},
        "http://fake",
        "fake-model",
        max_turns=1,
    )

    assert calls == 1
    assert result["turns"] == 1
    assert result["tool_calls"] == 1
    assert result["input_tokens"] == 17
    assert result["cached_input_tokens"] == 11
    assert result["final_message"] == "Agent reached the configured model-turn limit."


def test_run_honors_explicit_tool_call_limit(monkeypatch, tmp_path: Path) -> None:
    module = _module()
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    response = {
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "read-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"module.py"}',
                            },
                        },
                        {
                            "id": "read-2",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"missing.py"}',
                            },
                        },
                    ],
                }
            }
        ],
    }
    monkeypatch.setattr(module, "_json_request", lambda *_args, **_kwargs: response)

    result = module.run(
        {"workspace": str(tmp_path), "case_id": "generic-case"},
        "http://fake",
        "fake-model",
        max_turns=4,
        max_tool_calls=1,
    )

    assert result["turns"] == 1
    assert result["tool_calls"] == 1
    assert result["final_message"] == "Agent reached the configured tool-call limit."


def test_run_stops_before_a_second_request_at_cumulative_input_limit(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    calls = 0

    def fake_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "usage": {"prompt_tokens": 17, "completion_tokens": 3},
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "read-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"module.py"}',
                                },
                            }
                        ],
                    }
                }
            ],
        }

    monkeypatch.setattr(module, "_json_request", fake_request)
    result = module.run(
        {"workspace": str(tmp_path), "case_id": "generic-case"},
        "http://fake",
        "fake-model",
        max_turns=4,
        max_total_input_tokens=10,
    )

    assert calls == 1
    assert result["turns"] == 1
    assert result["input_tokens"] == 17
    assert result["final_message"] == "Agent reached the configured cumulative input-token limit."
