"""Executable split plans that keep adaptive and holdout benchmark evidence isolated."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Literal, Mapping, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentskill_eval_benchmark_gen.dataset import DatasetSplit
from agentskill_eval_benchmark_gen.spec import BenchmarkGenerationSpec, CandidateSpec
from agentskill_eval_benchmark_gen.split_audit import (
    SplitAuditEntry,
    SplitAuditError,
    SplitAuditReport,
    audit_split_entries,
)
from agentskill_eval_contracts import stable_sha256


class BenchmarkSplitPlanError(ValueError):
    """Raised when a split plan is incomplete or violates the exposure boundary."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SplitAssignments(StrictModel):
    train: Tuple[str, ...] = Field(min_length=1)
    validation_search: Tuple[str, ...] = Field(min_length=1)
    regression_dev: Tuple[str, ...] = Field(min_length=1)
    validation_confirm: Tuple[str, ...] = Field(min_length=1)
    locked_test: Tuple[str, ...] = Field(min_length=1)

    def by_split(self) -> Mapping[DatasetSplit, Tuple[str, ...]]:
        return {
            DatasetSplit.TRAIN: self.train,
            DatasetSplit.VALIDATION_SEARCH: self.validation_search,
            DatasetSplit.REGRESSION_DEV: self.regression_dev,
            DatasetSplit.VALIDATION_CONFIRM: self.validation_confirm,
            DatasetSplit.LOCKED_TEST: self.locked_test,
        }


class BenchmarkSplitPlan(StrictModel):
    """One complete candidate allocation with an explicit repository exposure policy."""

    schema_version: Literal["ase/benchmark-split-plan/v1alpha2"]
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_spec: Path
    repository_isolation: Literal["adaptive_vs_holdout"]
    locked_test_visibility: Literal["public_high_contamination", "private"]
    claim_limit: str = Field(min_length=1)
    splits: SplitAssignments

    @classmethod
    def load(cls, path: Path) -> "BenchmarkSplitPlan":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            plan = cls.model_validate(payload)
            root = path.resolve(strict=True).parent
            source_path = (
                plan.source_spec
                if plan.source_spec.is_absolute()
                else root / plan.source_spec
            )
            plan = plan.model_copy(update={"source_spec": source_path.resolve(strict=True)})
            plan.require_valid()
            return plan
        except (OSError, yaml.YAMLError, ValueError, SplitAuditError) as exc:
            raise BenchmarkSplitPlanError(f"invalid benchmark split plan {path}: {exc}") from exc

    def source(self) -> BenchmarkGenerationSpec:
        return BenchmarkGenerationSpec.load(self.source_spec)

    def entries(self) -> Tuple[SplitAuditEntry, ...]:
        spec = self.source()
        candidates = {item.key: item for item in spec.candidates}
        sources = {item.key: item for item in spec.repository_sources()}
        entries = []
        for split, case_ids in self.splits.by_split().items():
            for case_id in case_ids:
                candidate = candidates.get(case_id)
                if candidate is None:
                    raise BenchmarkSplitPlanError(f"unknown candidate in split plan: {case_id}")
                source = sources[candidate.source_key]
                family = candidate.provenance_family or candidate.after_commit
                entries.append(
                    SplitAuditEntry(
                        dataset_name=self.name,
                        dataset_version=self.version,
                        case_id=case_id,
                        split=split,
                        repository=source.repository_url,
                        fork_lineage=source.fork_lineage,
                        patch_family=family,
                        independence_group=f"{source.fork_lineage}#{family}",
                    )
                )
        return tuple(entries)

    def audit(self) -> SplitAuditReport:
        return audit_split_entries(self.entries())

    def semantic_sha256(self) -> str:
        return stable_sha256(
            {
                "schema_version": self.schema_version,
                "name": self.name,
                "version": self.version,
                "source_spec_sha256": stable_sha256(self.source().semantic_payload()),
                "repository_isolation": self.repository_isolation,
                "locked_test_visibility": self.locked_test_visibility,
                "claim_limit": self.claim_limit,
                "splits": {
                    split.value: case_ids
                    for split, case_ids in self.splits.by_split().items()
                },
            }
        )

    def require_valid(self) -> None:
        spec = self.source()
        expected = {item.key for item in spec.candidates}
        assigned = [case_id for values in self.splits.by_split().values() for case_id in values]
        duplicates = sorted(case_id for case_id in set(assigned) if assigned.count(case_id) > 1)
        if duplicates:
            raise BenchmarkSplitPlanError(
                f"candidate assigned to multiple splits: {','.join(duplicates)}"
            )
        missing = sorted(expected - set(assigned))
        unknown = sorted(set(assigned) - expected)
        if missing or unknown:
            raise BenchmarkSplitPlanError(
                f"split plan candidate mismatch: missing={missing}, unknown={unknown}"
            )
        self.audit().require_passed()

    def generation_spec(self, split: DatasetSplit) -> BenchmarkGenerationSpec:
        """Return the source spec filtered to exactly one audited split."""

        self.require_valid()
        spec = self.source()
        selected_ids = self.splits.by_split().get(split)
        if selected_ids is None:
            raise BenchmarkSplitPlanError(f"unsupported split in plan: {split.value}")
        candidates_by_id: Dict[str, CandidateSpec] = {item.key: item for item in spec.candidates}
        selected = tuple(candidates_by_id[case_id] for case_id in selected_ids)
        updates: Dict[str, object] = {
            "name": f"{self.name}-{split.value}",
            "version": self.version,
            "target_split": split.value,
            "candidates": selected,
            "split_plan_required": False,
            "split_plan_sha256": self.semantic_sha256(),
        }
        if spec.sources:
            source_keys = {item.source_key for item in selected}
            updates["sources"] = tuple(
                source for source in spec.sources if source.key in source_keys
            )
        return spec.model_copy(update=updates)
