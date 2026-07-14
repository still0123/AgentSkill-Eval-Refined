"""Leakage audit for datasets used across Skill search and promotion stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple

from agentskill_eval_benchmark_gen.dataset import CaseMetadata, DatasetSplit, LoadedDataset

PROTECTED_SPLITS = frozenset(
    {
        DatasetSplit.TRAIN,
        DatasetSplit.REGRESSION_DEV,
        DatasetSplit.VALIDATION_SEARCH,
        DatasetSplit.VALIDATION_CONFIRM,
        DatasetSplit.LOCKED_TEST,
    }
)


@dataclass(frozen=True)
class SplitAuditEntry:
    """The minimum immutable identity needed to detect cross-split leakage."""

    dataset_name: str
    dataset_version: str
    case_id: str
    split: DatasetSplit
    repository: str
    fork_lineage: str
    patch_family: str
    independence_group: str

    @classmethod
    def from_case(
        cls, dataset_name: str, dataset_version: str, case: CaseMetadata
    ) -> "SplitAuditEntry":
        groups = case.group_keys
        return cls(
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            case_id=case.case_id,
            split=case.split,
            repository=groups.repository,
            fork_lineage=groups.fork_lineage,
            patch_family=groups.patch_family,
            independence_group=groups.independence_group,
        )


@dataclass(frozen=True)
class SplitLeakage:
    key_type: str
    key_value: str
    splits: Tuple[DatasetSplit, ...]
    cases: Tuple[str, ...]


@dataclass(frozen=True)
class SplitAuditReport:
    entries: Tuple[SplitAuditEntry, ...]
    leakages: Tuple[SplitLeakage, ...]

    @property
    def passed(self) -> bool:
        return not self.leakages

    def require_passed(self) -> None:
        if self.leakages:
            summary = "; ".join(
                f"{item.key_type}={item.key_value} crosses "
                f"{','.join(split.value for split in item.splits)}"
                for item in self.leakages
            )
            raise SplitAuditError(f"dataset split leakage detected: {summary}")


class SplitAuditError(ValueError):
    """Raised when protected dataset splits share a case or source family."""


def audit_loaded_datasets(datasets: Iterable[LoadedDataset]) -> SplitAuditReport:
    entries = tuple(
        SplitAuditEntry.from_case(
            dataset.manifest.name, dataset.manifest.version, loaded_case.metadata
        )
        for dataset in datasets
        for loaded_case in dataset.cases
    )
    return audit_split_entries(entries)


def audit_split_entries(entries: Iterable[SplitAuditEntry]) -> SplitAuditReport:
    """Reject overlap between protected splits while allowing reuse inside one split."""

    frozen_entries = tuple(entries)
    leakages = []
    for key_type in (
        "case_id",
        "repository",
        "fork_lineage",
        "patch_family",
        "independence_group",
    ):
        grouped: Dict[str, list[SplitAuditEntry]] = {}
        for entry in frozen_entries:
            if entry.split not in PROTECTED_SPLITS:
                continue
            grouped.setdefault(getattr(entry, key_type), []).append(entry)
        for key_value, matching in grouped.items():
            splits = tuple(sorted({item.split for item in matching}, key=lambda item: item.value))
            if len(splits) <= 1:
                continue
            cases = tuple(sorted({item.case_id for item in matching}))
            leakages.append(
                SplitLeakage(
                    key_type=key_type,
                    key_value=key_value,
                    splits=splits,
                    cases=cases,
                )
            )
    ordered = tuple(
        sorted(leakages, key=lambda item: (item.key_type, item.key_value, item.cases))
    )
    return SplitAuditReport(entries=frozen_entries, leakages=ordered)


def split_inventory(entries: Iterable[SplitAuditEntry]) -> Mapping[DatasetSplit, int]:
    inventory: Dict[DatasetSplit, int] = {}
    for entry in entries:
        inventory[entry.split] = inventory.get(entry.split, 0) + 1
    return dict(sorted(inventory.items(), key=lambda item: item[0].value))
