#!/usr/bin/env python3
"""Run the preregistered local v1/v2 generalization matrix."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from agentskill_eval_contracts import RealRunMode
from agentskill_eval_real_evidence import RealAgentEvidenceRunner, RealAgentEvidenceSpec

EXPERIMENT = Path(__file__).resolve().parent
DATASETS = Path("/private/tmp/ase-v2-generalization-data/dataset-versions")
MATRIX = (
    (
        "more-itertools",
        "23fff68d-8e73-585f-b2f5-49180015a3a7",
        (
            "more-itertools-split-after-empty-tail",
            "more-itertools-last-reversed-none",
            "more-itertools-sample-strict-counts",
        ),
    ),
    (
        "cachetools",
        "851a3a28-4e33-56fe-a2b1-1c6c0cbd3c6b",
        (
            "cachetools-hashkey-pickle",
            "cachetools-cachedmethod-bound-key",
            "cachetools-ttl-boundary-expiration",
        ),
    ),
    (
        "boltons",
        "99fd667b-0657-5e3d-89d3-2411236ff258",
        (
            "boltons-split-maxsplit-zero",
            "boltons-bytes2human-boundary",
            "boltons-truncated-traceback",
        ),
    ),
    (
        "humanize",
        "c952da54-07da-5a78-bb25-efb5af66e331",
        (
            "humanize-scientific-negative-sign",
            "humanize-intcomma-zero-precision",
            "humanize-metric-precision-floor",
        ),
    ),
    (
        "pydash",
        "7fd3a9ec-acad-5415-9228-faab75ed4d4f",
        (
            "pydash-reversed-in-range",
            "pydash-dict-to-list",
            "pydash-false-math-operands",
        ),
    ),
    (
        "funcy",
        "cdb9f85b-d3a1-577f-b33d-8df2f5bafbbf",
        (
            "funcy-throttle-timedelta",
            "funcy-cache-invalidate-idempotent",
            "funcy-retry-list-errors",
            "funcy-cache-mixed-arguments",
        ),
    ),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spec(
    template: dict[str, Any],
    repository: str,
    dataset_id: str,
    case_ids: tuple[str, ...],
) -> RealAgentEvidenceSpec:
    payload = dict(template)
    payload["name"] = f"python-bug-fix-v2-generalization-{repository}"
    payload["dataset_path"] = str(DATASETS / dataset_id)
    payload["case_ids"] = list(case_ids)
    return RealAgentEvidenceSpec.model_validate(payload)


async def run(workspace: Path) -> list[dict[str, Any]]:
    template = yaml.safe_load(
        (EXPERIMENT / "runtime-smoke-cachetools.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(template, dict):
        raise ValueError("runtime template must be a YAML object")
    runner = RealAgentEvidenceRunner(workspace)
    summaries = []
    for repository, dataset_id, case_ids in MATRIX:
        spec = _spec(template, repository, dataset_id, case_ids)
        planned_runs = len(case_ids) * 2 * spec.protocol.evidence_repeats
        result = await runner.run(
            spec,
            RealRunMode.EVIDENCE,
            confirm_real_run=True,
            max_cost_microusd=planned_runs,
            max_agent_runs=planned_runs,
        )
        if result.report is None or result.report_json is None or result.replay_bundle is None:
            raise RuntimeError(f"{repository} produced no completed evidence report")
        summary = {
            "repository": repository,
            "experiment_id": str(result.manifest.experiment_id),
            "completed_runs": result.manifest.completed_runs,
            "invalid_runs": result.manifest.invalid_runs,
            "baseline_pass_rate": result.report.baseline_pass_rate,
            "treatment_pass_rate": result.report.treatment_pass_rate,
            "absolute_gain": result.report.absolute_gain,
            "wtl": result.report.wtl,
            "report_sha256": _sha256(result.report_json),
            "bundle_sha256": _sha256(result.replay_bundle),
        }
        summaries.append(summary)
        print(json.dumps(summary, sort_keys=True), flush=True)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--confirm-real-run", action="store_true")
    args = parser.parse_args()
    if not args.confirm_real_run:
        parser.error("--confirm-real-run is required")
    summaries = asyncio.run(run(args.workspace.resolve()))
    output = args.workspace / "expanded-generalization-summary.json"
    output.write_text(
        json.dumps(summaries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
