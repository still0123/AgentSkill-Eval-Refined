#!/usr/bin/env python3
"""Local process fixture for Agent adapter integration; never real evidence."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print("fake-process-agent 1.0.0")
        return 0
    counter_file = os.environ.get("FAKE_AGENT_COUNTER_FILE")
    call_number = 1
    if counter_file:
        path = Path(counter_file)
        current = int(path.read_text(encoding="utf-8")) if path.exists() else 0
        call_number = current + 1
        path.write_text(str(call_number), encoding="utf-8")
    request = json.load(sys.stdin)
    model = request["engine"]["model"]["name"]
    if model == "fake-timeout":
        time.sleep(5)
    skill_loaded = bool(request["skill_loaded"])
    invalid_from = int(os.environ.get("FAKE_AGENT_INVALID_FROM_CALL", "0"))
    provider_errors = {
        "fake-402": "DeepSeek HTTP 402 Insufficient Balance",
        "fake-429": "DeepSeek HTTP 429 rate limited",
        "fake-provider-timeout": "DeepSeek provider timeout",
    }
    error = provider_errors.get(model)
    if model == "fake-402-once" and call_number <= 4:
        error = "DeepSeek HTTP 402 Insufficient Balance"
    if (
        model == "fake-invalid"
        or (invalid_from and call_number >= invalid_from)
        or (model == "fake-invalid-after-four" and 5 <= call_number <= 6)
    ):
        error = "synthetic process Agent invalid result"
    status = "error" if error else ("pass" if skill_loaded else "fail")
    result = {
        "status": status,
        "response": (
            "Applied a minimal fix and observed the regression test pass."
            if skill_loaded
            else "Could not complete the repair within the turn budget."
        ),
        "duration_ms": 25,
        "turns": 2,
        "input_tokens": 120 if skill_loaded else 100,
        "output_tokens": 30,
        "cached_input_tokens": 0,
        "tool_calls": 3,
        "cost_microusd": (
            0
            if error in provider_errors.values()
            else 1_000
            if model == "fake-expensive"
            else 250
        ),
        "trace_events": [
            {"kind": "file.read", "payload": {"path": "production.py"}},
            {"kind": "command.run", "payload": {"command": "targeted offline test"}},
            {"kind": "test.result", "payload": {"passed": skill_loaded}},
        ],
    }
    if error:
        result["error"] = error
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
