"""Deterministic planning for local paired experiments."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple
from uuid import UUID, uuid5

from agentskill_eval_contracts import (
    ExecutionStatus,
    ExperimentManifest,
    ExperimentStatus,
    ExperimentVariant,
    PairBlock,
    Run,
    RunPlanFingerprint,
    stable_sha256,
)
from agentskill_eval_experiment.storage import ExperimentLayout, LocalExperimentStore


@dataclass(frozen=True)
class CaseExecutionSpec:
    id: UUID
    runner_case_id: str
    independence_group: str
    source_eval_dir: Path
    case_file: Path
    case_sha256: str
    grader_sha256: str
    platform_compiled_prompt_sha256: str


@dataclass(frozen=True)
class VariantRuntimeSpec:
    variant_id: UUID
    engine: Mapping[str, Any]
    environment: Mapping[str, Any]
    skill_path: Optional[Path] = None
    mcp: Mapping[str, Any] = field(default_factory=lambda: {"servers": []})
    collect_artifacts: Tuple[str, ...] = ()
    agent_home_files: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    secret_env: Mapping[str, str] = field(default_factory=dict, repr=False)
    timeout_seconds: int = 300
    max_turns: int = 10


@dataclass(frozen=True)
class PlannedBlock:
    block: PairBlock
    case: CaseExecutionSpec
    runs: Tuple[Run, ...]


@dataclass(frozen=True)
class PlannedExperiment:
    experiment: ExperimentManifest
    variants: Tuple[ExperimentVariant, ...]
    runtime_specs: Tuple[VariantRuntimeSpec, ...]
    blocks: Tuple[PlannedBlock, ...]

    def runtime_for(self, variant_id: UUID) -> VariantRuntimeSpec:
        matches = [item for item in self.runtime_specs if item.variant_id == variant_id]
        if len(matches) != 1:
            raise ValueError(f"expected one runtime spec for variant {variant_id}")
        return matches[0]


class LocalExperimentPlanner:
    def __init__(self, store: LocalExperimentStore) -> None:
        self.store = store

    def build(
        self,
        experiment: ExperimentManifest,
        variants: Sequence[ExperimentVariant],
        runtime_specs: Sequence[VariantRuntimeSpec],
        cases: Sequence[CaseExecutionSpec],
        *,
        repeats: int,
        random_seed: int,
        max_attempts: int = 1,
    ) -> PlannedExperiment:
        if experiment.status != ExperimentStatus.FROZEN:
            raise ValueError("experiment must be FROZEN before planning")
        if repeats < 1 or max_attempts < 1:
            raise ValueError("repeats and max_attempts must be positive")
        if len(variants) < 2:
            raise ValueError("paired experiments require at least two variants")
        if not cases:
            raise ValueError("an experiment requires at least one case")
        case_ids = [case.id for case in cases]
        runner_case_ids = [case.runner_case_id for case in cases]
        if len(case_ids) != len(set(case_ids)) or len(runner_case_ids) != len(set(runner_case_ids)):
            raise ValueError("case IDs and runner_case_ids must be unique")

        variant_by_id = {variant.id: variant for variant in variants}
        if len(variant_by_id) != len(variants):
            raise ValueError("variant IDs must be unique")
        reference_ids = tuple(reference.variant_id for reference in experiment.variants)
        if set(reference_ids) != set(variant_by_id):
            raise ValueError("experiment references must exactly match supplied variants")
        for reference in experiment.variants:
            if reference.variant_sha256 != variant_by_id[reference.variant_id].variant_sha256:
                raise ValueError("variant fingerprint does not match experiment reference")
        for variant in variants:
            if variant.experiment_id != experiment.id:
                raise ValueError("all variants must belong to the experiment")

        runtime_ids = [item.variant_id for item in runtime_specs]
        if len(runtime_ids) != len(set(runtime_ids)) or set(runtime_ids) != set(variant_by_id):
            raise ValueError("runtime specs must map one-to-one to variants")
        if any(item.timeout_seconds < 1 or item.max_turns < 1 for item in runtime_specs):
            raise ValueError("runtime timeout_seconds and max_turns must be positive")

        total_runs = len(cases) * repeats * len(variants)
        max_runs = experiment.budget_snapshot.get("max_runs")
        if isinstance(max_runs, int) and not isinstance(max_runs, bool) and total_runs > max_runs:
            raise ValueError(f"plan requires {total_runs} runs but budget allows {max_runs}")

        blocks = []
        for case in cases:
            self._validate_case(case)
            for repeat_index in range(repeats):
                block_id = uuid5(experiment.id, f"pair-block:{case.id}:{repeat_index}")
                block_seed = self._block_seed(random_seed, case.id, repeat_index)
                order = list(reference_ids)
                random.Random(block_seed).shuffle(order)
                block = PairBlock(
                    id=block_id,
                    experiment_id=experiment.id,
                    case_id=case.id,
                    independence_group=case.independence_group,
                    repeat_index=repeat_index,
                    seed=block_seed,
                    execution_order=tuple(order),
                )
                runs = tuple(
                    Run(
                        id=uuid5(block_id, f"run:{variant_id}"),
                        experiment_id=experiment.id,
                        pair_block_id=block_id,
                        variant_id=variant_id,
                        run_plan_fingerprint=self._fingerprint(
                            case,
                            variant_by_id[variant_id],
                            self._runtime_for(runtime_specs, variant_id),
                        ),
                        max_attempts=max_attempts,
                    )
                    for variant_id in order
                )
                blocks.append(PlannedBlock(block=block, case=case, runs=runs))
        return PlannedExperiment(
            experiment=experiment,
            variants=tuple(variant_by_id[variant_id] for variant_id in reference_ids),
            runtime_specs=tuple(runtime_specs),
            blocks=tuple(blocks),
        )

    def persist(self, plan: PlannedExperiment) -> None:
        self.store.save_experiment(plan.experiment)
        frozen_cases = set()
        for planned_block in plan.blocks:
            case = planned_block.case
            if case.id in frozen_cases:
                continue
            self.store.freeze_input_tree(
                plan.experiment.id, "case_source", case.id, case.source_eval_dir
            )
            frozen_cases.add(case.id)
        for runtime in plan.runtime_specs:
            if runtime.skill_path is not None and runtime.skill_path.is_dir():
                self.store.freeze_input_tree(
                    plan.experiment.id, "skill", runtime.variant_id, runtime.skill_path
                )
        for variant in plan.variants:
            self.store.save_variant(variant)
        for planned_block in plan.blocks:
            self.store.save_pair_block(planned_block.block)
            for run in planned_block.runs:
                layout = ExperimentLayout(self.store.workspace, plan.experiment.id)
                if layout.run(run.id).exists():
                    existing = self.store.load_run(plan.experiment.id, run.id)
                    if (
                        existing.pair_block_id != run.pair_block_id
                        or existing.variant_id != run.variant_id
                        or existing.run_plan_fingerprint != run.run_plan_fingerprint
                    ):
                        raise ValueError(f"persisted run {run.id} does not match the plan")
                    continue
                self.store.save_run(run)
                payload = run.model_dump(mode="python", round_trip=True)
                payload["execution_status"] = ExecutionStatus.QUEUED
                payload["queued_at"] = datetime.now(timezone.utc)
                queued = Run.model_validate(payload)
                self.store.save_run(queued)

    @staticmethod
    def _runtime_for(
        runtime_specs: Sequence[VariantRuntimeSpec], variant_id: UUID
    ) -> VariantRuntimeSpec:
        return next(item for item in runtime_specs if item.variant_id == variant_id)

    @staticmethod
    def _block_seed(random_seed: int, case_id: UUID, repeat_index: int) -> int:
        digest = stable_sha256(
            {"random_seed": random_seed, "case_id": str(case_id), "repeat_index": repeat_index}
        )
        return int(digest[:15], 16)

    @staticmethod
    def _fingerprint(
        case: CaseExecutionSpec,
        variant: ExperimentVariant,
        runtime: VariantRuntimeSpec,
    ) -> RunPlanFingerprint:
        upstream = stable_sha256(
            {
                "variant_sha256": variant.variant_sha256,
                "engine": dict(runtime.engine),
                "environment": dict(runtime.environment),
                "mcp": dict(runtime.mcp),
                "collect_artifacts": list(runtime.collect_artifacts),
                "agent_home_files": dict(runtime.agent_home_files),
                "timeout_seconds": runtime.timeout_seconds,
                "max_turns": runtime.max_turns,
            }
        )
        return RunPlanFingerprint(
            case_sha256=case.case_sha256,
            grader_sha256=case.grader_sha256,
            platform_compiled_prompt_sha256=case.platform_compiled_prompt_sha256,
            upstream_config_sha256=upstream,
            image_digest=variant.sandbox_snapshot.image_digest,
        )

    @staticmethod
    def _validate_case(case: CaseExecutionSpec) -> None:
        if not case.runner_case_id or not case.independence_group:
            raise ValueError("case identifiers cannot be empty")
        if not case.source_eval_dir.is_dir() or not case.case_file.is_file():
            raise ValueError("case source files must exist")
        for digest in (
            case.case_sha256,
            case.grader_sha256,
            case.platform_compiled_prompt_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("case fingerprints must be lowercase SHA-256 values")
