from __future__ import annotations

import pytest

from agentskill_eval_benchmark_gen.dataset import DatasetSplit
from agentskill_eval_benchmark_gen.split_audit import (
    SplitAuditEntry,
    SplitAuditError,
    audit_split_entries,
    require_common_split_plan_lineage,
    split_inventory,
)


def entry(
    case_id: str,
    split: DatasetSplit,
    *,
    repository: str,
    family: str,
) -> SplitAuditEntry:
    return SplitAuditEntry(
        dataset_name=f"dataset-{split.value}",
        dataset_version="1.0.0",
        case_id=case_id,
        split=split,
        repository=repository,
        fork_lineage=repository,
        patch_family=family,
        independence_group=f"{repository}/{family}",
    )


def test_distinct_source_families_pass_split_audit() -> None:
    entries = (
        entry("train-a", DatasetSplit.TRAIN, repository="repo-a", family="bug-a"),
        entry(
            "search-b",
            DatasetSplit.VALIDATION_SEARCH,
            repository="repo-b",
            family="bug-b",
        ),
        entry(
            "confirm-c",
            DatasetSplit.VALIDATION_CONFIRM,
            repository="repo-c",
            family="bug-c",
        ),
        entry(
            "locked-d", DatasetSplit.LOCKED_TEST, repository="repo-d", family="bug-d"
        ),
    )

    report = audit_split_entries(entries)

    assert report.passed
    report.require_passed()
    assert split_inventory(entries)[DatasetSplit.LOCKED_TEST] == 1


def test_repository_and_family_overlap_are_reported() -> None:
    entries = (
        entry("search-a", DatasetSplit.VALIDATION_SEARCH, repository="repo-a", family="bug-a"),
        entry("confirm-a", DatasetSplit.VALIDATION_CONFIRM, repository="repo-a", family="bug-a"),
    )

    report = audit_split_entries(entries)

    assert not report.passed
    assert {item.key_type for item in report.leakages} == {
        "repository",
        "fork_lineage",
        "patch_family",
        "independence_group",
    }
    with pytest.raises(SplitAuditError, match="dataset split leakage detected"):
        report.require_passed()


def test_reuse_inside_one_split_is_allowed() -> None:
    entries = (
        entry("search-a", DatasetSplit.VALIDATION_SEARCH, repository="repo-a", family="bug-a"),
        entry("search-b", DatasetSplit.VALIDATION_SEARCH, repository="repo-a", family="bug-a"),
    )

    assert audit_split_entries(entries).passed


def test_repository_reuse_inside_adaptive_zone_is_allowed_for_distinct_families() -> None:
    entries = (
        entry("train-a", DatasetSplit.TRAIN, repository="repo-a", family="bug-a"),
        entry(
            "search-b",
            DatasetSplit.VALIDATION_SEARCH,
            repository="repo-a",
            family="bug-b",
        ),
        entry(
            "regression-c",
            DatasetSplit.REGRESSION_DEV,
            repository="repo-a",
            family="bug-c",
        ),
    )

    assert audit_split_entries(entries).passed


def test_repository_reuse_inside_holdout_zone_is_allowed_for_frozen_candidate() -> None:
    entries = (
        entry(
            "confirm-a",
            DatasetSplit.VALIDATION_CONFIRM,
            repository="repo-b",
            family="bug-a",
        ),
        entry(
            "locked-b",
            DatasetSplit.LOCKED_TEST,
            repository="repo-b",
            family="bug-b",
        ),
    )

    assert audit_split_entries(entries).passed


def test_repository_crossing_adaptive_to_holdout_is_rejected_even_for_distinct_families() -> None:
    entries = (
        entry(
            "search-a",
            DatasetSplit.VALIDATION_SEARCH,
            repository="repo-a",
            family="bug-a",
        ),
        entry(
            "locked-b",
            DatasetSplit.LOCKED_TEST,
            repository="repo-a",
            family="bug-b",
        ),
    )

    report = audit_split_entries(entries)

    assert not report.passed
    assert {item.key_type for item in report.leakages} == {"repository", "fork_lineage"}
    with pytest.raises(SplitAuditError, match="exposure boundary"):
        report.require_passed()


def test_dataset_versions_require_one_frozen_split_plan_lineage() -> None:
    digest = "a" * 64

    assert require_common_split_plan_lineage(
        ({"split_plan_sha256": digest}, {"split_plan_sha256": digest})
    ) == digest

    with pytest.raises(SplitAuditError, match="lacks split-plan lineage"):
        require_common_split_plan_lineage(({}, {"split_plan_sha256": digest}))
    with pytest.raises(SplitAuditError, match="lineage mismatch"):
        require_common_split_plan_lineage(
            ({"split_plan_sha256": digest}, {"split_plan_sha256": "b" * 64})
        )
