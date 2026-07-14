from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agentskill_eval_benchmark_gen import (
    DatasetSplit,
    OptimizationBenchmarkPlan,
    OptimizationBenchmarkPublisher,
    OptimizationBenchmarkRelease,
    SplitDatasetReference,
)
from agentskill_eval_cli.main import app
from agentskill_eval_contracts import stable_sha256

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "examples/benchmark-sources/optimization-split-v1/plan.yaml"


def reference(split: DatasetSplit, index: int) -> SplitDatasetReference:
    digest = f"{index:064x}"
    return SplitDatasetReference(
        split=split,
        dataset_version_id=UUID(int=index),
        dataset_content_sha256=digest,
        relative_path=f"dataset-versions/{index}",
        case_count=4,
        candidate_keys=tuple(f"{split.value}-case-{value}" for value in range(4)),
        source_lineages=(f"github.com/example/repository-{index}",),
        independence_groups=tuple(f"family-{index}-{value}" for value in range(4)),
        command_evidence_count=48,
    )


def release() -> OptimizationBenchmarkRelease:
    refs = tuple(
        reference(split, index)
        for index, split in enumerate(
            (
                DatasetSplit.TRAIN,
                DatasetSplit.VALIDATION_SEARCH,
                DatasetSplit.REGRESSION_DEV,
                DatasetSplit.VALIDATION_CONFIRM,
                DatasetSplit.LOCKED_TEST,
            ),
            start=1,
        )
    )
    payload = {
        "schema_version": "ase/optimization-benchmark-release/v1",
        "name": "fixture",
        "version": "1",
        "plan_sha256": "a" * 64,
        "generator_version": "fixture",
        "verifier_version": "fixture",
        "total_case_count": 20,
        "repository_count": 5,
        "independence_group_count": 20,
        "splits": [item.model_dump(mode="json") for item in refs],
        "locked_policy": "withheld_until_one_shot_final_evaluation",
        "claim_limit": "fixture evidence only",
    }
    return OptimizationBenchmarkRelease.model_validate(
        {**payload, "content_sha256": stable_sha256(payload)}
    )


def test_checked_in_plan_has_twenty_cases_and_five_isolated_repositories(
    tmp_path: Path,
) -> None:
    plan = OptimizationBenchmarkPlan.load(PLAN)
    specs = OptimizationBenchmarkPublisher(tmp_path).validate_plan(plan, PLAN)

    assert sum(len(spec.candidates) for spec in specs) == 20
    assert len({spec.split_plan_sha256 for spec in specs}) == 1
    assert all(spec.split_plan_sha256 is not None for spec in specs)
    assert all(not spec.split_plan_required for spec in specs)
    assert {item.target_split for item in specs} == {
        "train",
        "validation_search",
        "regression_dev",
        "validation_confirm",
        "locked_test",
    }
    assert len(
        {
            source.repository_url
            for spec in specs
            for source in spec.repository_sources()
        }
    ) == 5
    assert len(
        {
            (source.fork_lineage, candidate.provenance_family)
            for spec in specs
            for source in spec.repository_sources()
            for candidate in spec.candidates
            if candidate.source_key == source.key
        }
    ) == 20


def test_optimizer_view_withholds_confirmation_and_locked_dataset_inputs() -> None:
    view = OptimizationBenchmarkPublisher.optimizer_view(release())

    assert [item["split"] for item in view["visible_splits"]] == [
        "train",
        "validation_search",
        "regression_dev",
    ]
    assert [item["split"] for item in view["withheld_splits"]] == [
        "validation_confirm",
        "locked_test",
    ]
    serialized = str(view["withheld_splits"])
    assert "relative_path" not in serialized
    assert "candidate_keys" not in serialized
    assert view["locked_test_accessed"] is False


def test_release_content_hash_detects_manifest_tampering() -> None:
    payload = release().model_dump(mode="json")
    payload["claim_limit"] = "tampered"

    with pytest.raises(ValidationError, match="content hash mismatch"):
        OptimizationBenchmarkRelease.model_validate(payload)


def test_split_cli_validates_plan_and_requires_explicit_offline_publication(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    validated = runner.invoke(
        app,
        ["benchmark", "split", "validate", str(PLAN), "--workspace", str(tmp_path)],
    )
    refused = runner.invoke(
        app,
        [
            "benchmark",
            "split",
            "publish",
            str(PLAN),
            "--workspace",
            str(tmp_path),
            "--reviewer",
            "fixture-reviewer",
            "--publisher",
            "fixture-publisher",
        ],
    )

    assert validated.exit_code == 0
    assert '"case_count": 20' in validated.stdout
    assert refused.exit_code != 0
    # Rich may wrap the long option name at different columns across Python/terminal versions.
    # Assert the security effect instead: refusal happens before any paid-or-offline work exists.
    assert not (tmp_path / "benchmark-jobs").exists()
