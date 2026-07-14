"""Leakage audit for datasets used across Skill search and promotion stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
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


class ExposureZone(str, Enum):
    """Whether a split may influence Skill selection or only evaluates a frozen winner."""

    ADAPTIVE = "adaptive"
    HOLDOUT = "holdout"


SPLIT_EXPOSURE_ZONES = {
    DatasetSplit.TRAIN: ExposureZone.ADAPTIVE,
    DatasetSplit.REGRESSION_DEV: ExposureZone.ADAPTIVE,
    DatasetSplit.VALIDATION_SEARCH: ExposureZone.ADAPTIVE,
    DatasetSplit.VALIDATION_CONFIRM: ExposureZone.HOLDOUT,
    DatasetSplit.LOCKED_TEST: ExposureZone.HOLDOUT,
}


def exposure_zone(split: DatasetSplit) -> ExposureZone:
    """Return the immutable exposure zone for a protected split."""

    try:
        return SPLIT_EXPOSURE_ZONES[split]
    except KeyError as exc:
        raise SplitAuditError(f"split has no exposure-zone policy: {split.value}") from exc


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
                (
                    f"{item.key_type}={item.key_value} crosses exposure boundary "
                    f"({','.join(split.value for split in item.splits)})"
                    if item.key_type in {"repository", "fork_lineage"}
                    else f"{item.key_type}={item.key_value} crosses "
                    f"{','.join(split.value for split in item.splits)}"
                )
                for item in self.leakages
            )
            raise SplitAuditError(f"dataset split leakage detected: {summary}")


class SplitAuditError(ValueError):
    """Raised when protected dataset splits share a case or source family."""


def require_common_split_plan_lineage(metadata_items: Iterable[Mapping[str, str]]) -> str:
    """Require every DatasetVersion to derive from the same frozen complete split plan."""

    hashes = []
    for metadata in metadata_items:
        value = metadata.get("split_plan_sha256")
        if value is None:
            raise SplitAuditError("DatasetVersion lacks split-plan lineage")
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise SplitAuditError("DatasetVersion has invalid split-plan lineage")
        hashes.append(value)
    if not hashes:
        raise SplitAuditError("no DatasetVersion split-plan lineage was provided")
    if len(set(hashes)) != 1:
        raise SplitAuditError("DatasetVersion split-plan lineage mismatch")
    return hashes[0]


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
    """Reject identity overlap between splits and repository overlap between exposure zones.

    Development-visible splits may share a repository because all of them can influence the Skill.
    Confirmation and locked test may share a repository only after the candidate hash is frozen.
    Repositories and fork lineages may never cross from adaptive development into the holdout zone;
    Case, patch-family, and independence identities remain unique across every protected split.
    """

    frozen_entries = tuple(entries)
    leakages = []
    for key_type in ("case_id", "patch_family", "independence_group"):
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
    for key_type in ("repository", "fork_lineage"):
        grouped = {}
        for entry in frozen_entries:
            if entry.split not in PROTECTED_SPLITS:
                continue
            grouped.setdefault(getattr(entry, key_type), []).append(entry)
        for key_value, matching in grouped.items():
            if len({exposure_zone(item.split) for item in matching}) <= 1:
                continue
            splits = tuple(sorted({item.split for item in matching}, key=lambda item: item.value))
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
