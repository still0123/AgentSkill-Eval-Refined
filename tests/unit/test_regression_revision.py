from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import RegressionDevCandidateRelease
from agentskill_eval_cli.main import app
from agentskill_eval_contracts import stable_sha256


def release_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ase/regression-dev-candidate-release/v1alpha1",
        "name": "fixture",
        "version": "2026.07.15.1",
        "plan_sha256": "a" * 64,
        "base_optimization_release_sha256": "b" * 64,
        "dataset_version_id": str(UUID(int=1)),
        "dataset_content_sha256": "c" * 64,
        "dataset_relative_path": "dataset-versions/fixture",
        "repository_lineages": ["github.com/Suor/funcy"],
        "independence_groups": ["funcy#one", "funcy#two", "funcy#three", "funcy#four"],
        "case_ids": ["case-1", "case-2", "case-3", "case-4"],
        "generator_version": "0.2.0",
        "verifier_version": "0.2.0",
        "required_observed_baseline_failures": 1,
        "status": "AWAITING_OBSERVED_BASELINE_SCREENING",
        "simulated": False,
        "validation_confirm_accessed": False,
        "locked_test_accessed": False,
        "claim_limit": "offline dataset construction only",
    }
    payload["content_sha256"] = stable_sha256(payload)
    return payload


def test_release_manifest_hash_rejects_tampering() -> None:
    release = RegressionDevCandidateRelease.model_validate(release_payload())
    tampered = release.model_dump(mode="json")
    tampered["claim_limit"] = "changed"

    with pytest.raises(ValidationError, match="content hash mismatch"):
        RegressionDevCandidateRelease.model_validate(tampered)


def test_regression_dev_publish_requires_offline_confirmation(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "regression-dev",
            "publish",
            "examples/benchmark-sources/regression-dev-v2/plan.yaml",
            "--workspace",
            str(tmp_path),
            "--reviewer",
            "reviewer",
            "--publisher",
            "publisher",
        ],
    )

    assert result.exit_code != 0
    assert not (tmp_path / "benchmark-jobs").exists()
