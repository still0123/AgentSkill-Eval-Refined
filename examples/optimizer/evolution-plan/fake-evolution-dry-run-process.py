#!/usr/bin/env python3
"""Local metadata-only Stage 3B integration fixture; never runs an Agent."""

from __future__ import annotations

import json
import sys

if "--version" in sys.argv:
    print("agentskill-eval-fake-evolution-dry-run 1.0")
    raise SystemExit(0)

request = json.load(sys.stdin)
if request.get("stage") not in {"validation_search", "regression_dev"}:
    raise SystemExit("protected stages are forbidden at the dry-run Process boundary")

print(
    json.dumps(
        {
            "schema_version": "ase/evolution-dry-run-process-response/v1alpha1",
            "dry_run_id": request["dry_run_id"],
            "stage": request["stage"],
            "dataset_version_sha256": request["dataset_version_sha256"],
            "accepted": True,
        },
        sort_keys=True,
    )
)
