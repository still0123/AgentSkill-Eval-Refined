"""Real-Agent candidate evaluator built on the existing paired evidence runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, MutableMapping, Optional, Sequence, Tuple
from uuid import UUID, uuid5

import yaml

from agentskill_eval_benchmark_gen import DatasetLoader
from agentskill_eval_contracts import (
    CandidateEvaluation,
    EvaluationOutcome,
    RealEvidenceStatus,
    RealRunMode,
    SearchCaseResult,
    SearchEvaluationStage,
    VariantRole,
    stable_sha256,
)
from agentskill_eval_experiment import ExperimentLayout, LocalExperimentStore
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_real_evidence import (
    BaselineReplay,
    RealAgentEvidenceRunner,
    RealAgentEvidenceSpec,
)
from agentskill_eval_skill_optimizer.spec import SearchCase


class RealCandidateEvaluationError(RuntimeError):
    """Raised when observed candidate evaluation cannot produce auditable results."""


@dataclass
class RealEvaluationAuthorization:
    """One explicit, process-local authorization shared by the complete search."""

    confirm_real_run: bool
    max_cost_microusd: int
    max_agent_runs: int
    consumed_cost_microusd: int = 0
    consumed_agent_runs: int = 0

    def remaining_cost(self) -> int:
        return self.max_cost_microusd - self.consumed_cost_microusd

    def remaining_runs(self) -> int:
        return self.max_agent_runs - self.consumed_agent_runs

    def reserve_completed(self, runs: int, cost_microusd: int) -> None:
        self.consumed_agent_runs += runs
        self.consumed_cost_microusd += cost_microusd


class RealAgentCandidateEvaluator:
    """Evaluate immutable Skill candidates using paired baseline/treatment runs."""

    def __init__(
        self,
        config_path: Path,
        workspace: Path,
        authorization: RealEvaluationAuthorization,
        *,
        baseline_skill_path: Optional[Path] = None,
        baseline_replay_cache: Optional[MutableMapping[str, BaselineReplay]] = None,
    ) -> None:
        self.template = RealAgentEvidenceSpec.load(config_path)
        if self.template.simulated:
            raise RealCandidateEvaluationError("real candidate evaluator refuses simulated config")
        if not authorization.confirm_real_run:
            raise RealCandidateEvaluationError("real candidate evaluation requires confirmation")
        if authorization.max_agent_runs < 1 or authorization.max_cost_microusd < 1:
            raise RealCandidateEvaluationError("positive Run and cost limits are required")
        self.workspace = workspace.resolve() / "real-optimizer-evidence"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.authorization = authorization
        self.baseline_skill_path = (
            baseline_skill_path.resolve(strict=True) if baseline_skill_path is not None else None
        )
        self.baseline_replay_cache = (
            baseline_replay_cache if baseline_replay_cache is not None else {}
        )
        self._baseline_results: Dict[str, SearchCaseResult] = {}
        self.writer = AtomicFileWriter()
        self.cache_path = self.workspace / "candidate-case-cache.json"
        self._cache: Dict[str, object] = self._load_cache()
        self._sha = stable_sha256(
            {
                "type": "real_agent",
                "config": self.template.model_dump(
                    mode="json", exclude={"name", "dataset_path", "skill_path", "case_ids"}
                ),
            }
        )

    @property
    def evaluator_sha256(self) -> str:
        return self._sha

    @property
    def simulated(self) -> bool:
        return False

    @property
    def baseline_results(self) -> Dict[str, SearchCaseResult]:
        """Return the first observed v1 result for each Case, never a candidate result."""
        return dict(self._baseline_results)

    def authorize_plan(self, candidate_case_evaluations: int) -> Tuple[int, int]:
        """Reject the complete plan before the first external Agent call."""
        agent_runs = candidate_case_evaluations * 2
        estimated_cost = agent_runs * self.template.pricing.estimated_cost_per_run_microusd
        if self.authorization.remaining_runs() < agent_runs:
            raise RealCandidateEvaluationError(
                f"real optimizer requires {agent_runs} Agent Runs but authorization allows "
                f"{self.authorization.remaining_runs()}"
            )
        if self.authorization.remaining_cost() < estimated_cost:
            raise RealCandidateEvaluationError(
                f"real optimizer estimates {estimated_cost} microusd but authorization allows "
                f"{self.authorization.remaining_cost()}"
            )
        return agent_runs, estimated_cost

    def evaluate(
        self,
        skill_file: Path,
        dataset_file: Path,
        dataset_sha256: str,
        cases: Sequence[SearchCase],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
    ) -> CandidateEvaluation:
        del timeout_seconds
        dataset_root = dataset_file.parent
        loaded = DatasetLoader().load(dataset_root)
        if loaded.dataset_sha256 != dataset_sha256:
            raise RealCandidateEvaluationError("real evaluator DatasetVersion hash mismatch")
        skill_root, skill_sha = self._skill_package(skill_file)
        results: Dict[str, SearchCaseResult] = {}
        pending = []
        for case in cases:
            key = self._cache_key(skill_sha, dataset_sha256, case.id)
            cached = self._cache.get(key)
            if cached is None:
                pending.append(case)
            else:
                results[case.id] = SearchCaseResult.model_validate(cached)
        if len(pending) % 2:
            raise RealCandidateEvaluationError(
                "real candidate evaluation requires an even number of uncached cases"
            )
        for index in range(0, len(pending), 2):
            pair = (pending[index], pending[index + 1])
            pair_results = self._run_pair(
                skill_root, skill_sha, dataset_root, dataset_sha256, pair
            )
            for item in pair_results:
                results[item.case_id] = item
                self._cache[self._cache_key(skill_sha, dataset_sha256, item.case_id)] = (
                    item.model_dump(mode="json")
                )
            self._save_cache()
        ordered = tuple(results[item.id] for item in cases)
        return self._aggregate(ordered, dataset_sha256, stage)

    def _run_pair(
        self,
        skill_root: Path,
        skill_sha: str,
        dataset_root: Path,
        dataset_sha256: str,
        cases: Tuple[SearchCase, SearchCase],
    ) -> Tuple[SearchCaseResult, SearchCaseResult]:
        planned_runs = 4
        estimated_cost = planned_runs * self.template.pricing.estimated_cost_per_run_microusd
        if self.authorization.remaining_runs() < planned_runs:
            raise RealCandidateEvaluationError("real optimizer Agent Run budget exhausted")
        if self.authorization.remaining_cost() < estimated_cost:
            raise RealCandidateEvaluationError("real optimizer cost budget exhausted")
        pair_ids = (cases[0].id, cases[1].id)
        spec = self.template.model_copy(
            update={
                "name": f"optimizer-{skill_sha[:12]}-{stable_sha256(pair_ids)[:12]}",
                "dataset_path": dataset_root,
                "skill_path": skill_root,
                "baseline_skill_path": self.baseline_skill_path,
                "case_ids": pair_ids,
            }
        )
        runner = RealAgentEvidenceRunner(self.workspace)
        result = asyncio.run(
            runner.run(
                spec,
                RealRunMode.SMOKE,
                confirm_real_run=True,
                max_cost_microusd=self.authorization.remaining_cost(),
                max_agent_runs=self.authorization.remaining_runs(),
                baseline_replay_cache=self.baseline_replay_cache,
            )
        )
        if result.manifest.status != RealEvidenceStatus.COMPLETED:
            raise RealCandidateEvaluationError(
                f"real evidence pair ended as {result.manifest.status.value}"
            )
        self.authorization.reserve_completed(
            result.manifest.completed_runs
            + result.manifest.invalid_runs
            - result.manifest.reused_runs,
            result.manifest.observed_or_reserved_cost_microusd,
        )
        baseline, treatment = self._variant_results(
            result.manifest.experiment_id, dataset_root, pair_ids
        )
        for item in baseline:
            self._baseline_results.setdefault(item.case_id, item)
        return treatment

    def _variant_results(
        self, experiment_id: UUID, dataset_root: Path, case_ids: Tuple[str, str]
    ) -> Tuple[
        Tuple[SearchCaseResult, SearchCaseResult],
        Tuple[SearchCaseResult, SearchCaseResult],
    ]:
        store = LocalExperimentStore(self.workspace)
        loaded = DatasetLoader().load(dataset_root)
        case_by_uuid = {
            uuid5(loaded.dataset_id, f"case:{item.metadata.case_id}"): item.metadata.case_id
            for item in loaded.cases
        }
        baseline_ids = {
            item.id
            for item in store.list_variants(experiment_id)
            if item.role == VariantRole.BASELINE
        }
        treatment_ids = {
            item.id
            for item in store.list_variants(experiment_id)
            if item.role == VariantRole.TREATMENT
        }
        found: Dict[str, Dict[str, SearchCaseResult]] = {
            "baseline": {},
            "treatment": {},
        }
        layout = ExperimentLayout(self.workspace, experiment_id)
        for run in store.list_runs(experiment_id):
            role = (
                "baseline"
                if run.variant_id in baseline_ids
                else "treatment"
                if run.variant_id in treatment_ids
                else None
            )
            if role is None:
                continue
            block = store.load_pair_block(experiment_id, run.pair_block_id)
            case_id = case_by_uuid[block.case_id]
            attempt = store.load_selected_attempt(experiment_id, run)
            measurement = store.load_selected_measurement(experiment_id, run)
            if attempt is None:
                raise RealCandidateEvaluationError(f"treatment Run {run.id} has no Attempt")
            outcome = run.evaluation_outcome or EvaluationOutcome.INVALID
            trace = layout.trace_manifest(run.id, attempt.attempt_no)
            diagnosis = layout.failure_diagnosis(run.id, attempt.attempt_no)
            found[role][case_id] = SearchCaseResult(
                case_id=case_id,
                passed=outcome == EvaluationOutcome.PASS,
                score=run.final_score or 0,
                input_tokens=(measurement.input_tokens or 0) if measurement else 0,
                output_tokens=(measurement.output_tokens or 0) if measurement else 0,
                latency_ms=(measurement.duration_ms or 0) if measurement else 0,
                cost_microusd=measurement.cost_microusd if measurement else None,
                outcome=outcome.value,
                experiment_id=experiment_id,
                run_id=run.id,
                attempt_id=attempt.id,
                trace_ref=str(trace) if trace.is_file() else None,
                failure_diagnosis_ref=str(diagnosis) if diagnosis.is_file() else None,
            )
        expected = set(case_ids)
        if set(found["baseline"]) != expected or set(found["treatment"]) != expected:
            raise RealCandidateEvaluationError("real evidence returned the wrong paired cases")
        return (
            (found["baseline"][case_ids[0]], found["baseline"][case_ids[1]]),
            (found["treatment"][case_ids[0]], found["treatment"][case_ids[1]]),
        )

    def _skill_package(self, skill_file: Path) -> Tuple[Path, str]:
        content = skill_file.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        root = self.workspace / "candidate-skills" / digest
        target = root / "SKILL.md"
        metadata = root / "metadata.yaml"
        if target.exists():
            if target.read_bytes() != content:
                raise RealCandidateEvaluationError("candidate Skill package integrity mismatch")
            return root, digest
        root.mkdir(parents=True, exist_ok=True)
        self.writer.write(target, content)
        self.writer.write(
            metadata,
            yaml.safe_dump(
                {
                    "name": f"optimizer-candidate-{digest[:12]}",
                    "version": "candidate",
                    "skill_md_sha256": digest,
                    "license": "evaluation-only",
                },
                sort_keys=True,
            ).encode(),
        )
        return root, digest

    def _cache_key(self, skill_sha: str, dataset_sha: str, case_id: str) -> str:
        return stable_sha256(
            {
                "evaluator": self.evaluator_sha256,
                "skill": skill_sha,
                "dataset": dataset_sha,
                "case": case_id,
            }
        )

    def _load_cache(self) -> Dict[str, object]:
        if not self.cache_path.exists():
            return {}
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RealCandidateEvaluationError("invalid candidate-case cache")
        return payload

    def _save_cache(self) -> None:
        self.writer.write(
            self.cache_path,
            json.dumps(self._cache, sort_keys=True, separators=(",", ":")).encode() + b"\n",
        )

    def _aggregate(
        self,
        results: Tuple[SearchCaseResult, ...],
        dataset_sha256: str,
        stage: SearchEvaluationStage,
    ) -> CandidateEvaluation:
        costs = [item.cost_microusd for item in results]
        total_cost = None if any(item is None for item in costs) else sum(
            item for item in costs if item is not None
        )
        return CandidateEvaluation(
            stage=stage,
            dataset_sha256=dataset_sha256,
            evaluator_sha256=self.evaluator_sha256,
            case_ids=tuple(item.case_id for item in results),
            results=results,
            pass_rate=sum(item.passed for item in results) / len(results),
            mean_score=sum(item.score for item in results) / len(results),
            total_tokens=sum(item.input_tokens + item.output_tokens for item in results),
            total_latency_ms=sum(item.latency_ms for item in results),
            total_cost_microusd=total_cost,
            simulated=False,
            evaluated_at=datetime.now(timezone.utc),
        )
