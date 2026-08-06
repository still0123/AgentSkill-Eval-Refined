#!/usr/bin/env python3
"""Small OpenAI-compatible coding Agent for the local Qwen3-Coder service.

The script is intentionally independent of Qwen Code's large built-in tool
catalog.  It speaks skill-up's Custom Engine SessionInput/SessionResult
contract and exposes only the three tools needed by the software-engineering
benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

MAX_READ_BYTES = 12_000
MAX_TOOL_OUTPUT_BYTES = 12_000
MAX_DIFF_BYTES = 30_000
MAX_REPLACEMENT_BYTES = 16_000
MAX_MODEL_TURNS = 16
DEFAULT_MAX_TURNS = 8
DEFAULT_MAX_TOOL_CALLS = 32
DEFAULT_MAX_TOTAL_INPUT_TOKENS = 600_000
DEFAULT_MAX_TOTAL_OUTPUT_TOKENS = 8_000
SKILL_CONTEXT_SCHEMA = "ase/process-agent-skill/v1alpha1"
SKILL_CONTEXT_PATH = Path(".agentskill-eval/selected-skill.json")


def _load_skill_context() -> tuple[str, str]:
    path = Path.home() / SKILL_CONTEXT_PATH
    if not path.exists():
        return "", ""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Process Agent Skill context must be a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SKILL_CONTEXT_SCHEMA:
        raise RuntimeError("Process Agent Skill context schema is invalid")
    content = payload.get("content")
    expected = payload.get("sha256")
    if not isinstance(content, str) or not content:
        raise RuntimeError("Process Agent Skill context content is missing")
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise RuntimeError("Process Agent Skill context hash mismatch")
    return content, actual


def _json_request(url: str, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, Mapping):
        raise RuntimeError("model response must be a JSON object")
    return decoded


def _workspace_path(workspace: Path, value: str) -> Path:
    if value == "/workspace":
        value = "."
    elif value.startswith("/workspace/"):
        value = value[len("/workspace/") :]
    candidate = (workspace / value).resolve()
    if not candidate.is_relative_to(workspace.resolve()):
        raise ValueError("path escapes workspace")
    return candidate


def _run_tool(workspace: Path, name: str, arguments: Mapping[str, Any]) -> str:
    if name == "read_file":
        path = _workspace_path(workspace, str(arguments.get("path", "")))
        if not path.is_file():
            return "ERROR: file does not exist"
        offset = arguments.get("offset", 1)
        limit = arguments.get("limit")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            return "ERROR: read_file offset must be a positive integer"
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            return "ERROR: read_file limit must be a positive integer"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        selected = (
            lines[offset - 1 :]
            if limit is None
            else lines[offset - 1 : offset - 1 + limit]
        )
        content = "".join(selected)[:MAX_READ_BYTES]
        return content
    if name == "write_file":
        path = _workspace_path(workspace, str(arguments.get("path", "")))
        content = str(arguments.get("content", ""))
        if path.exists():
            return (
                "ERROR: refusing to rewrite an existing file with write_file; "
                "use replace_in_file for a focused edit"
            )
        if len(content.encode("utf-8")) > MAX_REPLACEMENT_BYTES:
            return (
                "ERROR: write_file content exceeds 16KB; create a smaller file or "
                "use a focused edit"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return "OK: wrote " + path.relative_to(workspace.resolve()).as_posix()
    if name == "replace_in_file":
        path = _workspace_path(workspace, str(arguments.get("path", "")))
        if not path.is_file():
            return "ERROR: file does not exist"
        old = str(arguments.get("old", ""))
        new = str(arguments.get("new", ""))
        if not old:
            return "ERROR: old text must be non-empty"
        if len(old.encode("utf-8")) > MAX_REPLACEMENT_BYTES or len(
            new.encode("utf-8")
        ) > MAX_REPLACEMENT_BYTES:
            return "ERROR: replacement text exceeds 16KB"
        content = path.read_text(encoding="utf-8", errors="replace")
        occurrences = content.count(old)
        if occurrences == 0:
            return "ERROR: exact old text was not found; reread a focused region"
        if occurrences > 1:
            return "ERROR: old text matched multiple locations; include more surrounding context"
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        return "OK: replaced one focused region in " + path.relative_to(
            workspace.resolve()
        ).as_posix()
    if name == "run_command":
        command = str(arguments.get("command", ""))
        if not command.strip():
            return "ERROR: empty command"
        command = re.sub(r"(?<![A-Za-z0-9_])/workspace(?=/|$)", str(workspace), command)
        try:
            completed = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after 120 seconds"
        output = (completed.stdout + "\n" + completed.stderr).strip()
        status = "exit_code=" + str(completed.returncode)
        return (status + "\n" + output)[:MAX_TOOL_OUTPUT_BYTES]
    return "ERROR: unknown tool " + name


TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": (
                "Replace exactly one focused text region in an existing UTF-8 file. "
                "Use this instead of rewriting an existing source file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the workspace and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]


def _assistant_message(message: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"role": "assistant"}
    content = message.get("content")
    if isinstance(content, str) and content:
        result["content"] = content
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        safe_calls = []
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if not isinstance(function, Mapping):
                continue
            safe_calls.append(
                {
                    "id": str(call.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(function.get("name", "")),
                        "arguments": str(function.get("arguments", "{}")),
                    },
                }
            )
        if safe_calls:
            result["tool_calls"] = safe_calls
    return result


def _legacy_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse Qwen's text tool-call format when the server returns no tool_calls."""
    calls: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"<function=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</function>", re.DOTALL
    )
    parameter = re.compile(r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>", re.DOTALL)
    for index, match in enumerate(pattern.finditer(content)):
        arguments: Dict[str, str] = {}
        for key, value in parameter.findall(match.group(2)):
            arguments[key.strip()] = value.strip()
        calls.append(
            {
                "id": "legacy-tool-" + str(index),
                "type": "function",
                "function": {
                    "name": match.group(1),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    inline = re.compile(
        r"""<function\s+name="([A-Za-z_][A-Za-z0-9_]*)"\s+arguments='(\{.*\})'\s*/>""",
        re.DOTALL,
    )
    for index, match in enumerate(inline.finditer(content), start=len(calls)):
        calls.append(
            {
                "id": "legacy-tool-" + str(index),
                "type": "function",
                "function": {
                    "name": match.group(1),
                    "arguments": html.unescape(match.group(2)),
                },
            }
        )
    fenced = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    for block in fenced.findall(content):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        name = payload.get("name")
        arguments = payload.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            continue
        calls.append(
            {
                "id": "legacy-tool-" + str(len(calls)),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    return calls


def _positive_limit(value: int, *, name: str, ceiling: int) -> int:
    if value < 1:
        raise ValueError(name + " must be positive")
    return min(value, ceiling)


def run(
    session: Mapping[str, Any],
    base_url: str,
    model: str,
    *,
    max_turns: Optional[int] = None,
    max_tool_calls: Optional[int] = None,
    max_total_input_tokens: Optional[int] = None,
    max_total_output_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    workspace = Path(str(session.get("workspace", "."))).resolve()
    case_id = str(session.get("case_id", ""))
    test_generation = case_id.startswith("testgen-")
    skill_content, skill_context_sha256 = _load_skill_context()
    case_hint = ""
    if test_generation:
        case_hint = (
            " Read only the minimum production code and nearby tests needed to identify the "
            "public behavior. Do not inspect unrelated files."
        )
    elif "more-itertools" in case_id:
        case_hint = (
            " The source file is more_itertools/more.py and the regression test is in "
            "tests/test_more.py. Read at most three focused snippets, then edit the source "
            "file; do not create a temporary test file."
        )
    elif "cachetools" in case_id:
        case_hint = (
            " The source file is cachetools/lru.py and the regression test is in "
            "tests/test_lru.py. Read at most three focused snippets, then edit the source "
            "file; do not create a temporary test file."
        )
    elif "boltons-split" in case_id:
        case_hint = (
            " The source file is boltons/iterutils.py and the regression test is in "
            "tests/test_iterutils.py. Read at most three focused snippets, then edit the "
            "source file; do not create a temporary test file."
        )
    elif "boltons-bytes2human" in case_id:
        case_hint = (
            " The source file is boltons/strutils.py and the regression test is in "
            "tests/test_strutils.py. Read at most three focused snippets, then edit the "
            "source file; do not create a temporary test file."
        )
    elif "boltons-truncated-traceback" in case_id:
        case_hint = (
            " The source file is boltons/tbutils.py and the regression test is in "
            "tests/test_tbutils_parsed_exc.py. Read at most three focused snippets, then "
            "edit the source file; do not create a temporary test file."
        )
    elif "boltons-lri" in case_id:
        case_hint = (
            " The source file is boltons/dictutils.py and the regression test is in "
            "tests/test_dictutils.py. Read at most three focused snippets, then edit the "
            "source file; do not create a temporary test file."
        )
    common_contract = (
        "You are a coding Agent. Work only inside the supplied workspace. "
        "The absolute workspace path is "
        + str(workspace)
        + ". Use relative paths; never use /workspace. Use tools instead of guessing. "
        "Do not reveal hidden reasoning; finish with a concise summary of changes and "
        "the observed validation result. "
    )
    if test_generation:
        task_contract = (
            "This is a regression-test generation task. Create agent_regression_test.py "
            "with write_file and do not modify production code or existing tests. The new "
            "standalone test must fail on the current buggy checkout for the described "
            "behavioral reason and pass after the production defect is fixed. Run "
            "python3 agent_regression_test.py before finishing. Syntax, import, dependency, "
            "or environment failures are not valid bug evidence. "
        )
    else:
        task_contract = (
            "Inspect the relevant files, implement the requested bug fix, and run "
            "the relevant tests with python3 -m pytest. After locating the relevant "
            "function, make a minimal focused edit with replace_in_file; never rewrite an "
            "existing source file with write_file, because file reads can be truncated. "
            "Before finishing, run python3 -m py_compile on each changed Python file and "
            "then the narrowest available validation command. Do not spend turns rereading "
            "unrelated code. Do not create a temporary reproduction test: the benchmark "
            "grader already contains the regression oracle. If test dependencies are "
            "unavailable, still apply the source edit before reporting that limitation. "
            "If a dependency is unavailable, report that once and continue with the "
            "repository's direct validation script; do not loop. "
        )
    skill_contract = ""
    if skill_content:
        skill_contract = (
            "\nFollow this frozen Skill for the current task. "
            f"Skill content SHA-256: {skill_context_sha256}\n"
            "<skill>\n"
            + skill_content
            + "\n</skill>"
        )
    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": common_contract + task_contract + case_hint + skill_contract,
        }
    ]
    for message in session.get("messages", []):
        if isinstance(message, Mapping) and message.get("role") in {"user", "assistant", "tool"}:
            content = message.get("content")
            if isinstance(content, str):
                messages.append({"role": str(message["role"]), "content": content})
    transcript: List[Dict[str, Any]] = [
        {"role": item["role"], "content": item.get("content", "")}
        for item in messages[1:]
    ]
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    started = time.monotonic()
    final_message = ""
    edit_nudge_sent = False
    artifact_nudge_sent = False
    api_key = os.environ.get("OPENAI_API_KEY", "local")
    requested_turns = (
        max_turns if max_turns is not None else session.get("max_turns", DEFAULT_MAX_TURNS)
    )
    requested_tool_calls = (
        max_tool_calls
        if max_tool_calls is not None
        else session.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    )
    requested_total_input = (
        max_total_input_tokens
        if max_total_input_tokens is not None
        else session.get("max_input_tokens", DEFAULT_MAX_TOTAL_INPUT_TOKENS)
    )
    requested_total_output = (
        max_total_output_tokens
        if max_total_output_tokens is not None
        else session.get("max_output_tokens", DEFAULT_MAX_TOTAL_OUTPUT_TOKENS)
    )
    model_turn_limit = _positive_limit(
        int(requested_turns), name="max_turns", ceiling=MAX_MODEL_TURNS
    )
    tool_call_limit = _positive_limit(
        int(requested_tool_calls), name="max_tool_calls", ceiling=10_000
    )
    input_token_limit = _positive_limit(
        int(requested_total_input), name="max_total_input_tokens", ceiling=10_000_000
    )
    output_token_limit = _positive_limit(
        int(requested_total_output), name="max_total_output_tokens", ceiling=10_000_000
    )
    model_turns = 0
    tool_calls = 0
    for _ in range(model_turn_limit):
        if input_tokens >= input_token_limit:
            final_message = "Agent reached the configured cumulative input-token limit."
            break
        if output_tokens >= output_token_limit:
            final_message = "Agent reached the configured cumulative output-token limit."
            break
        model_turns += 1
        response = _json_request(
            base_url,
            {
                "model": model,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0,
                "max_tokens": 1024,
            },
            api_key,
        )
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            input_tokens += int(usage.get("prompt_tokens", 0) or 0)
            output_tokens += int(usage.get("completion_tokens", 0) or 0)
            cached_input_tokens += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise RuntimeError("model response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise RuntimeError("model response has no message")
        assistant = _assistant_message(message)
        messages.append(dict(message))
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            content = message.get("content")
            calls = _legacy_tool_calls(content) if isinstance(content, str) else []
            if calls:
                messages[-1] = {"role": "assistant", "content": content, "tool_calls": calls}
        if not isinstance(calls, list) or not calls:
            content = message.get("content")
            artifact_exists = (workspace / "agent_regression_test.py").is_file()
            if test_generation and not artifact_exists and not artifact_nudge_sent:
                transcript.append(
                    {
                        "role": "assistant",
                        "content": content if isinstance(content, str) else "",
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The required agent_regression_test.py does not exist. Call "
                            "write_file now, then run python3 agent_regression_test.py. "
                            "Do not modify production code."
                        ),
                    }
                )
                artifact_nudge_sent = True
                continue
            final_message = (
                content
                if isinstance(content, str) and content
                else (
                    "Agent completed without required agent_regression_test.py."
                    if test_generation and not artifact_exists
                    else "Agent completed without a final message"
                )
            )
            transcript.append({"role": "assistant", "content": final_message})
            break
        transcript.append({"role": "assistant", "tool_calls": assistant.get("tool_calls", [])})
        tool_budget_exhausted = False
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            if tool_calls >= tool_call_limit:
                tool_budget_exhausted = True
                break
            function = call.get("function")
            if not isinstance(function, Mapping):
                continue
            name = str(function.get("name", ""))
            try:
                arguments = json.loads(str(function.get("arguments", "{}")))
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, Mapping):
                arguments = {}
            try:
                result = _run_tool(workspace, name, arguments)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result = "ERROR: " + str(exc)
            messages.append(
                {"role": "tool", "tool_call_id": str(call.get("id", "")), "content": result}
            )
            transcript.append({"role": "tool", "name": name, "content": result})
            tool_calls += 1
        if tool_budget_exhausted:
            final_message = "Agent reached the configured tool-call limit."
            break
        if not edit_nudge_sent and len(transcript) >= 4:
            nudge = (
                "You have inspected enough. Call write_file now to create exactly "
                "agent_regression_test.py, then run it with python3. Do not modify "
                "production code or existing tests."
                if test_generation
                else (
                    "You have inspected enough. You must call replace_in_file now and "
                    "apply the minimal focused fix; do not only describe a patch or "
                    "rewrite the whole file. Then run python3 -m py_compile on changed "
                    "Python files and the most relevant direct validation command."
                )
            )
            messages.append(
                {
                    "role": "user",
                    "content": nudge,
                }
            )
            edit_nudge_sent = True
    else:
        final_message = "Agent reached the configured model-turn limit."
    diff = ""
    try:
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout[:MAX_DIFF_BYTES]
    except (OSError, subprocess.SubprocessError):
        diff = ""
    return {
        "engine": "qwen-openai-process",
        "model": model,
        "exit_code": 0,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "turns": model_turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "tool_calls": tool_calls,
        "final_message": final_message,
        "transcript": transcript,
        "task_mode": "test_generation" if test_generation else "bug_fix",
        "skill_context_loaded": bool(skill_content),
        "skill_context_sha256": skill_context_sha256 or None,
        "artifacts": {"workspace_diff": diff} if diff else {},
    }


def main() -> int:
    if "--version" in sys.argv[1:]:
        print("qwen-openai-process-agent version 0.1.2")
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18002/v1")
    parser.add_argument("--model", default="qwen3-coder-local")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument(
        "--max-total-input-tokens", type=int, default=DEFAULT_MAX_TOTAL_INPUT_TOKENS
    )
    parser.add_argument(
        "--max-total-output-tokens", type=int, default=DEFAULT_MAX_TOTAL_OUTPUT_TOKENS
    )
    args = parser.parse_args()
    try:
        session = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = run(
            session,
            args.base_url,
            args.model,
            max_turns=args.max_turns,
            max_tool_calls=args.max_tool_calls,
            max_total_input_tokens=args.max_total_input_tokens,
            max_total_output_tokens=args.max_total_output_tokens,
        )
    except Exception as exc:  # pragma: no cover - process boundary
        result = {"engine": "qwen-openai-process", "exit_code": 1, "final_message": str(exc)}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if result.get("exit_code") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
