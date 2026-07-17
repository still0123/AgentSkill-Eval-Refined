#!/usr/bin/env python3
"""Pinned skill-up CLI fixture that delegates one Case to fake_process_agent.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print("skill-up version 0.5.0")
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "validate":
        json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        return 0
    if len(sys.argv) < 3 or sys.argv[1] != "run":
        print("unsupported fake skill-up invocation", file=sys.stderr)
        return 2
    eval_path = Path(sys.argv[2]).resolve(strict=True)
    output_index = sys.argv.index("--output-dir") + 1
    output_root = Path(sys.argv[output_index]) / "iteration-1"
    output_root.mkdir(parents=True, exist_ok=True)
    config = json.loads(eval_path.read_text(encoding="utf-8"))
    compiled_root = eval_path.parent.parent
    case_path = compiled_root / config["cases"]["files"][0]
    case_text = case_path.read_text(encoding="utf-8")
    case_id = next(
        line.split(":", 1)[1].strip().strip("\"'")
        for line in case_text.splitlines()
        if line.startswith("id:")
    )
    agent = os.environ.get("AGENTSKILL_EVAL_AGENT_EXECUTABLE")
    if not agent:
        print("missing configured Agent executable", file=sys.stderr)
        return 3
    request = {
        "case_id": case_id,
        "prompt": "Execute the frozen offline repair task and verify it.",
        "engine": config["engine"],
        "skill_loaded": bool(config["skills"]),
    }
    completed = subprocess.run(
        (agent,),
        input=json.dumps(request).encode(),
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        sys.stderr.buffer.write(completed.stderr)
        return completed.returncode
    agent_result = json.loads(completed.stdout)
    payload = {
        "case_results": [
            {
                "case_id": case_id,
                "status": agent_result["status"],
                "response": agent_result["response"],
                "duration_ms": agent_result["duration_ms"],
                "turns": agent_result["turns"],
                "input_tokens": agent_result["input_tokens"],
                "output_tokens": agent_result["output_tokens"],
                "cached_input_tokens": agent_result["cached_input_tokens"],
                "tool_calls": agent_result["tool_calls"],
                "cost_microusd": agent_result["cost_microusd"],
                "grading": {"score": 1 if agent_result["status"] == "pass" else 0},
                "error": agent_result.get("error"),
            }
        ],
        "trace_events": agent_result["trace_events"],
    }
    (output_root / "result.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
