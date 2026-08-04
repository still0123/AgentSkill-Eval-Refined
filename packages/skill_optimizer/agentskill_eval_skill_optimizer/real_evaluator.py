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
    RealEvidenceRunManifest,
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

    def __init__(
        self,
        message: str,
        *,
        manifest: Optional[RealEvidenceRunManifest] = None,
        baseline_results: Sequence[SearchCaseResult] = (),
        treatment_results: Sequence[SearchCaseResult] = (),
        reused_baseline_runs: int = 0,
    ) -> None:
        super().__init__(message)
        self.manifest = manifest
        self.baseline_results = tuple(baseline_results)
        self.treatment_results = tuple(treatment_results)
        self.reused_baseline_runs = reused_baseline_runs

    @property
    def has_observed_work(self) -> bool:
        return self.manifest is not None or bool(
            self.baseline_results or self.treatment_results
        )


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
        attempt_no: int = 1,
    ) -> None:
        self.template = RealAgentEvidenceSpec.load(config_path)
        if self.template.simulated:
            raise RealCandidateEvaluationError("real candidate evaluator refuses simulated config")
        if not authorization.confirm_real_run:
            raise RealCandidateEvaluationError("real candidate evaluation requires confirmation")
        if authorization.max_agent_runs < 1 or authorization.max_cost_microusd < 1:
            raise RealCandidateEvaluationError("positive Run and cost limits are required")
        if attempt_no < 1:
            raise RealCandidateEvaluationError(
                "candidate evaluation attempt number must be positive"
            )
        self.workspace = workspace.resolve() / "real-optimizer-evidence"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.authorization = authorization
        self.attempt_no = attempt_no
        self.baseline_skill_path = (
            baseline_skill_path.resolve(strict=True) if baseline_skill_path is not None else None
        )
        self.baseline_replay_cache = (
            baseline_replay_cache if baseline_replay_cache is not None else {}
        )
        self._baseline_results: Dict[str, SearchCaseResult] = {}
        self._reused_baseline_runs = 0
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

    @property
    def reused_baseline_runs(self) -> int:
        """Actual replay count observed by the evidence runner for this candidate."""
        return self._reused_baseline_runs

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
                cached_result = SearchCaseResult.model_validate(cached)
                # Invalid output can represent a transient provider block.  It
                # is evidence for the prior attempt, but must never suppress a
                # later *explicitly authorized* retry.
                if cached_result.outcome == "invalid":
                    pending.append(case)
                else:
                    results[case.id] = cached_result
        for index in range(0, len(pending), 2):
            pair = tuple(pending[index : index + 2])
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
        cases: Tuple[SearchCase, ...],
    ) -> Tuple[SearchCaseResult, ...]:
        if not 1 <= len(cases) <= 2:
            raise RealCandidateEvaluationError("real evaluator batches one or two cases")
        # A candidate pair has two logical baseline Runs and two treatment Runs.
        # Once the immutable v1 baseline is cached, only the treatment Runs are
        # new external calls.  The global authorization must therefore reserve
        # the number of uncached baseline/treatment Runs, not the logical pair
        # size.  The underlying evidence runner still receives a four-Run
        # logical cap because replayed baseline Runs remain in its audit plan.
        baseline_replay_namespace = self._baseline_replay_namespace(dataset_sha256)
        reused_baselines = sum(
            self._baseline_cache_key(baseline_replay_namespace, case.id)
            in self.baseline_replay_cache
            for case in cases
        )
        planned_runs = len(cases) * 2 - reused_baselines
        estimated_cost = planned_runs * self.template.pricing.estimated_cost_per_run_microusd
        if self.authorization.remaining_runs() < planned_runs:
            raise RealCandidateEvaluationError("real optimizer Agent Run budget exhausted")
        if self.authorization.remaining_cost() < estimated_cost:
            raise RealCandidateEvaluationError("real optimizer cost budget exhausted")
        pair_ids = tuple(item.id for item in cases)
        spec = self.template.model_copy(
            update={
                "name": (
                    f"optimizer-{skill_sha[:12]}-{stable_sha256(pair_ids)[:12]}"
                    f"-attempt-{self.attempt_no}"
                ),
                "dataset_path": dataset_root,
                "skill_path": skill_root,
                "baseline_skill_path": self.baseline_skill_path,
                "case_ids": pair_ids,
            }
        )
        runner = RealAgentEvidenceRunner(self.workspace)
        logical_pair_runs = len(cases) * 2
        logical_pair_cost = (
            logical_pair_runs * self.template.pricing.estimated_cost_per_run_microusd
        )
        result = asyncio.run(
            runner.run(
                spec,
                RealRunMode.SMOKE,
                confirm_real_run=True,
                max_cost_microusd=max(
                    logical_pair_cost, self.authorization.remaining_cost()
                ),
                max_agent_runs=max(4, self.authorization.remaining_runs()),
                baseline_replay_cache=self.baseline_replay_cache,
                baseline_replay_namespace=baseline_replay_namespace,
            )
        )
        self._reused_baseline_runs += result.manifest.reused_runs
        self.authorization.reserve_completed(
            max(
                0,
                result.manifest.completed_runs
                + result.manifest.invalid_runs
                - result.manifest.reused_runs,
            ),
            result.manifest.observed_or_reserved_cost_microusd,
        )
        try:
            observed_baseline, observed_treatment = self._observed_variant_results(
                result.manifest.experiment_id, dataset_root
            )
        except RealCandidateEvaluationError as exc:
            raise RealCandidateEvaluationError(
                str(exc),
                manifest=result.manifest,
                reused_baseline_runs=result.manifest.reused_runs,
            ) from exc
        for item in observed_baseline.values():
            self._baseline_results.setdefault(item.case_id, item)
        if result.manifest.status != RealEvidenceStatus.COMPLETED:
            raise RealCandidateEvaluationError(
                f"real evidence pair ended as {result.manifest.status.value}",
                manifest=result.manifest,
                baseline_results=tuple(
                    observed_baseline[case_id]
                    for case_id in pair_ids
                    if case_id in observed_baseline
                ),
                treatment_results=tuple(
                    observed_treatment[case_id]
                    for case_id in pair_ids
                    if case_id in observed_treatment
                ),
                reused_baseline_runs=result.manifest.reused_runs,
            )
        baseline, treatment = self._require_complete_pair(
            observed_baseline, observed_treatment, pair_ids
        )
        return treatment

    def _baseline_replay_namespace(self, dataset_sha256: str) -> str:
        return stable_sha256(
            {
                "baseline_skill_sha256": self._skill_sha(self.baseline_skill_path),
                "dataset_sha256": dataset_sha256,
                "evaluator_sha256": self.evaluator_sha256,
            }
        )

    @staticmethod
    def _baseline_cache_key(namespace: str, case_id: str) -> str:
        return stable_sha256({"namespace": namespace, "case_id": case_id})

    def _require_complete_pair(
        self,
        found_baseline: Dict[str, SearchCaseResult],
        found_treatment: Dict[str, SearchCaseResult],
        case_ids: Tuple[str, ...],
    ) -> Tuple[
        Tuple[SearchCaseResult, ...],
        Tuple[SearchCaseResult, ...],
    ]:
        expected = set(case_ids)
        if set(found_baseline) != expected or set(found_treatment) != expected:
            raise RealCandidateEvaluationError("real evidence returned the wrong paired cases")
        return (
            tuple(found_baseline[case_id] for case_id in case_ids),
            tuple(found_treatment[case_id] for case_id in case_ids),
        )

    def _observed_variant_results(
        self, experiment_id: UUID, dataset_root: Path
    ) -> Tuple[Dict[str, SearchCaseResult], Dict[str, SearchCaseResult]]:
        store = LocalExperimentStore(self.workspace)
        loaded = DatasetLoader().load(dataset_root)
        dataset_identity = (
            loaded.dataset_version.id if loaded.dataset_version else loaded.dataset_id
        )
        case_by_uuid = {
            uuid5(dataset_identity, f"case:{item.metadata.case_id}"): item.metadata.case_id
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
                continue
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
        return found["baseline"], found["treatment"]

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

    @staticmethod
    def _skill_sha(path: Optional[Path]) -> Optional[str]:
        if path is None:
            return None
        file = path / "SKILL.md" if path.is_dir() else path
        return hashlib.sha256(file.read_bytes()).hexdigest()

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
