#!/usr/bin/env python3
"""Deterministic local fixture for the audited Process Generator boundary."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

GUIDANCE = {
    "TASK_UNDERSTANDING": (
        "process-task-understanding",
        "Canonical boundary checks reduce task interpretation mismatches.",
        "Normalize at producer and consumer boundaries, then compare the canonical values.",
    ),
    "PLANNING": (
        "process-planning",
        "Exceptional-path planning reduces incomplete resource analysis.",
        "For resources, verify cleanup on every exception path before reporting a leak.",
    ),
    "TOOL_RECOVERY": (
        "process-tool-recovery",
        "Explicit retry accounting prevents recovery budget mistakes.",
        "Derive the attempt count from the configured retry budget before judging the loop.",
    ),
    "VERIFICATION": (
        "process-verification",
        "A runtime evidence gate reduces unsupported findings.",
        "Require reachable runtime evidence before reporting any actionable defect.",
    ),
}


def main() -> int:
    if "--version" in sys.argv:
        print("ase-fake-process-generator 1.0.0")
        return 0
    mode = os.environ.get("ASE_FAKE_GENERATOR_MODE", "ok")
    if mode == "timeout":
        time.sleep(10)
    if mode == "invalid-json":
        print("not-json")
        return 0
    counter = os.environ.get("ASE_PROCESS_GENERATOR_COUNTER")
    if counter:
        path = Path(counter)
        observed = int(path.read_text(encoding="utf-8")) if path.exists() else 0
        path.write_text(str(observed + 1), encoding="utf-8")
    request = json.load(sys.stdin)
    labels = sorted({item["label"] for item in request["eligible_failures"]})
    proposals = []
    for label in labels:
        if label not in GUIDANCE:
            continue
        identifier, hypothesis, instruction = GUIDANCE[label]
        proposals.append(
            {
                "id": identifier,
                "failure_label": label,
                "hypothesis": hypothesis,
                "instruction": instruction,
                "risks": ["Generated guidance requires independent regression validation."],
            }
        )
    if mode == "ineligible":
        proposals[0]["failure_label"] = "JUDGE"
    print(
        json.dumps(
            {
                "schema_version": "ase/process-hypothesis-response/v1alpha1",
                "hypotheses": proposals[: request["max_hypotheses"]],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
