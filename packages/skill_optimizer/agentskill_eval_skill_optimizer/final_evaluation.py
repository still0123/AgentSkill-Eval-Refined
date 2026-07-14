"""Independent paired evaluation for a frozen Skill-search winner."""

from __future__ import annotations

import hashlib
import html
import os
import random
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml

from agentskill_eval_benchmark_gen import (
    DatasetLoader,
    DatasetSplit,
    LoadedDataset,
    SplitAuditError,
    require_common_split_plan_lineage,
)
from agentskill_eval_contracts import (
    CandidateEvaluation,
    CandidateOrigin,
    FinalCaseComparison,
    FinalDecision,
    FinalEvaluationJob,
    FinalEvaluationReport,
    FinalEvaluationStage,
    FinalEvaluationStatus,
    LockedTestReceipt,
    OptimizationJobStatus,
    PairClassification,
    SearchCaseResult,
    SearchEvaluationStage,
    SkillCandidate,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter, fsync_directory
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes
from agentskill_eval_skill_optimizer.evaluator import CandidateEvaluator, ProcessEvaluator
from agentskill_eval_skill_optimizer.final_spec import (
    IndependentFinalEvaluationSpec,
    SimulatedFinalDataset,
)
from agentskill_eval_skill_optimizer.search import OptimizationStore
from agentskill_eval_skill_optimizer.spec import SearchCase


class FinalEvaluationError(RuntimeError):
    """Raised when isolation, integrity, or one-shot policies are violated."""


@dataclass(frozen=True)
class FinalEvaluationResult:
    job: FinalEvaluationJob
    report: FinalEvaluationReport
    report_json: Path
    report_html: Path


@dataclass(frozen=True)
class _PreparedDataset:
    source: Path
    sha256: str
    cases: Tuple[SearchCase, ...]
    groups: Mapping[str, str]
    simulated: bool
    curated: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _FinalKeywordEvaluator:
    """Offline controller simulation; never represents Agent performance evidence."""

    def __init__(self, dataset: SimulatedFinalDataset, version: str) -> None:
        self.dataset = dataset
        self._sha = stable_sha256(
            {"type": "simulated_keyword", "version": version, "dataset": dataset.model_dump()}
        )

    @property
    def evaluator_sha256(self) -> str:
        return self._sha

    @property
    def simulated(self) -> bool:
        return True

    def evaluate(
        self,
        skill_file: Path,
        dataset_file: Path,
        dataset_sha256: str,
        cases: Sequence[SearchCase],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
    ) -> CandidateEvaluation:
        del dataset_file, timeout_seconds
        definitions = {item.id: item for item in self.dataset.cases}
        content = skill_file.read_text(encoding="utf-8").lower()
        content_bytes = len(content.encode("utf-8"))
        results = []
        for case in cases:
            terms = definitions[case.id].required_terms
            matched = sum(term.lower() in content for term in terms)
            score = matched / len(terms)
            results.append(
                SearchCaseResult(
                    case_id=case.id,
                    passed=score == 1.0,
                    score=score,
                    input_tokens=max(1, content_bytes // 4),
                    output_tokens=24,
                    latency_ms=10 + content_bytes // 100,
                    cost_microusd=0,
                )
            )
        return _evaluation(tuple(results), dataset_sha256, self._sha, stage, simulated=True)


def _evaluation(
    results: Tuple[SearchCaseResult, ...],
    dataset_sha256: str,
    evaluator_sha256: str,
    stage: SearchEvaluationStage,
    *,
    simulated: bool,
) -> CandidateEvaluation:
    costs = [item.cost_microusd for item in results]
    total_cost = None if any(item is None for item in costs) else sum(
        item for item in costs if item is not None
    )
    return CandidateEvaluation(
        stage=stage,
        dataset_sha256=dataset_sha256,
        evaluator_sha256=evaluator_sha256,
        case_ids=tuple(item.case_id for item in results),
        results=results,
        pass_rate=sum(item.passed for item in results) / len(results),
        mean_score=sum(item.score for item in results) / len(results),
        total_tokens=sum(item.input_tokens + item.output_tokens for item in results),
        total_latency_ms=sum(item.latency_ms for item in results),
        total_cost_microusd=total_cost,
        simulated=simulated,
        evaluated_at=_utcnow(),
    )


class FinalEvaluationStore:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "final-evaluations"
        self.writer = AtomicFileWriter()

    def job_dir(self, job_id: UUID) -> Path:
        return self.root / "jobs" / str(job_id)

    def save_job(self, job: FinalEvaluationJob) -> None:
        self.writer.write(self.job_dir(job.id) / "job.json", model_bytes(job))

    def load_job(self, job_id: UUID) -> FinalEvaluationJob:
        return load_model((self.job_dir(job_id) / "job.json").read_bytes(), FinalEvaluationJob)

    def save_report(self, report: FinalEvaluationReport) -> Tuple[Path, Path]:
        directory = self.job_dir(report.job.id) / "reports"
        report_json = directory / "final-report.json"
        report_html = directory / "final-report.html"
        report_bytes = model_bytes(report)
        self.writer.write(report_json, report_bytes)
        self.writer.write(
            directory / "final-report.sha256", (_sha256(report_bytes) + "\n").encode()
        )
        self.writer.write(report_html, self._html(report).encode("utf-8"))
        return report_json, report_html

    def load_report(self, job_id: UUID) -> FinalEvaluationReport:
        directory = self.job_dir(job_id) / "reports"
        report_bytes = (directory / "final-report.json").read_bytes()
        expected = (directory / "final-report.sha256").read_text(encoding="utf-8").strip()
        if _sha256(report_bytes) != expected:
            raise FinalEvaluationError("persisted final report integrity mismatch")
        return load_model(report_bytes, FinalEvaluationReport)

    def reserve_locked_test(self, receipt: LockedTestReceipt) -> None:
        target = self.root / "locked-test-receipts" / f"{receipt.optimization_job_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = model_bytes(receipt)
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            existing = load_model(target.read_bytes(), LockedTestReceipt)
            if existing.final_evaluation_job_id == receipt.final_evaluation_job_id:
                return
            raise FinalEvaluationError(
                "locked_test was already consumed for this optimization job"
            ) from exc
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(target.parent)

    @staticmethod
    def _html(report: FinalEvaluationReport) -> str:
        rows = []
        for item in report.cases:
            rows.append(
                "<tr>"
                f"<td>{html.escape(item.case_id)}</td>"
                f"<td>{html.escape(item.independence_group)}</td>"
                f"<td>{item.base_pass_rate:.3f}</td>"
                f"<td>{item.winner_pass_rate:.3f}</td>"
                f"<td>{html.escape(item.classification.value)}</td>"
                "</tr>"
            )
        return (
            "<!doctype html><meta charset='utf-8'><title>Independent Final Evaluation</title>"
            "<h1>Independent Final Evaluation</h1>"
            f"<p>Stage: {html.escape(report.job.stage.value)}</p>"
            f"<p>Decision: {html.escape(report.decision.value)}</p>"
            f"<p>Base: {report.base_pass_rate:.3f}; winner: {report.winner_pass_rate:.3f}; "
            f"gain: {report.absolute_gain:+.3f}</p>"
            f"<p>{html.escape(report.decision_reason)}</p>"
            f"<p><strong>Claim limit:</strong> {html.escape(report.claim_limit)}</p>"
            "<table border='1'><thead><tr><th>Case</th><th>Group</th><th>Base</th>"
            "<th>Winner</th><th>W/T/L</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )


class IndependentFinalEvaluator:
    """Evaluate exactly one frozen base/winner pair on an unseen final split."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.optimization_store = OptimizationStore(self.workspace)
        self.store = FinalEvaluationStore(self.workspace)

    def run(self, spec: IndependentFinalEvaluationSpec) -> FinalEvaluationResult:
        optimization = self.optimization_store.load_job(spec.optimization_job_id)
        if (
            optimization.status != OptimizationJobStatus.FROZEN
            or optimization.frozen_winner_id is None
        ):
            raise FinalEvaluationError("final evaluation requires a frozen optimization job")
        candidates = self.optimization_store.list_candidates(optimization)
        original = self._one_candidate(candidates, CandidateOrigin.ORIGINAL)
        winner = next(
            (item for item in candidates if item.id == optimization.frozen_winner_id), None
        )
        if winner is None:
            raise FinalEvaluationError("frozen winner is missing")
        self.optimization_store.assert_skill_integrity(original)
        self.optimization_store.assert_skill_integrity(winner)

        prepared, simulated_dataset = self._prepare_dataset(spec, optimization.id)
        evaluator: CandidateEvaluator
        if spec.evaluator.type == "simulated_keyword":
            if simulated_dataset is None:
                raise FinalEvaluationError("simulated evaluator requires a simulated final dataset")
            evaluator = _FinalKeywordEvaluator(simulated_dataset, spec.evaluator.version)
        else:
            evaluator = ProcessEvaluator(spec.evaluator)
        optimization_sha = _sha256(model_bytes(optimization))
        semantic_spec = spec.model_dump(mode="json", exclude={"dataset_path"})
        spec_sha = stable_sha256(semantic_spec)
        identity = stable_sha256(
            {
                "optimization_job_sha256": optimization_sha,
                "spec_sha256": spec_sha,
                "dataset_sha256": prepared.sha256,
                "evaluator_sha256": evaluator.evaluator_sha256,
                "base_skill_sha256": original.content_sha256,
                "winner_skill_sha256": winner.content_sha256,
            }
        )
        job_id = uuid5(NAMESPACE_URL, f"ase-final-evaluation:{identity}")
        if self.store.job_dir(job_id).exists():
            return self._load_completed(job_id)

        job = FinalEvaluationJob(
            id=job_id,
            optimization_job_id=optimization.id,
            optimization_job_sha256=optimization_sha,
            status=FinalEvaluationStatus.RUNNING,
            stage=FinalEvaluationStage(spec.stage),
            spec_sha256=spec_sha,
            dataset_sha256=prepared.sha256,
            evaluator_sha256=evaluator.evaluator_sha256,
            base_skill_sha256=original.content_sha256,
            winner_skill_sha256=winner.content_sha256,
            winner_candidate_id=winner.id,
            repeats=spec.repeats,
            simulated=evaluator.simulated,
            created_at=_utcnow(),
        )
        self.store.save_job(job)
        job_dir = self.store.job_dir(job.id)
        input_dir = job_dir / "inputs"
        base_path = input_dir / "base-SKILL.md"
        winner_path = input_dir / "winner-SKILL.md"
        self.store.writer.write(
            base_path, self.optimization_store.skill_path(original).read_bytes()
        )
        self.store.writer.write(
            winner_path, self.optimization_store.skill_path(winner).read_bytes()
        )
        dataset_file = self._freeze_dataset(prepared, input_dir)

        try:
            if job.stage == FinalEvaluationStage.LOCKED_TEST:
                self.store.reserve_locked_test(
                    LockedTestReceipt(
                        optimization_job_id=optimization.id,
                        final_evaluation_job_id=job.id,
                        dataset_sha256=prepared.sha256,
                        evaluator_sha256=evaluator.evaluator_sha256,
                        consumed_at=_utcnow(),
                    )
                )
            base_results, winner_results = self._paired_runs(
                job,
                evaluator,
                prepared,
                dataset_file,
                base_path,
                winner_path,
                spec.timeout_seconds,
            )
            comparisons = self._comparisons(base_results, winner_results, prepared.groups)
            report = self._report(job, spec, base_results, winner_results, comparisons)
        except Exception:
            self.store.save_job(job.model_copy(update={"status": FinalEvaluationStatus.FAILED}))
            raise
        self.store.save_job(report.job)
        report_json, report_html = self.store.save_report(report)
        return FinalEvaluationResult(report.job, report, report_json, report_html)

    def _prepare_dataset(
        self, spec: IndependentFinalEvaluationSpec, optimization_job_id: UUID
    ) -> Tuple[_PreparedDataset, Optional[SimulatedFinalDataset]]:
        source = spec.dataset_path.resolve(strict=True)
        expected_split = DatasetSplit(spec.stage)
        if spec.evaluator.type == "simulated_keyword":
            if not source.is_file() or source.name != "final-validation.yaml":
                raise FinalEvaluationError(
                    "simulated dataset_path must name final-validation.yaml"
                )
            dataset = SimulatedFinalDataset.load(source)
            if dataset.split != spec.stage:
                raise FinalEvaluationError("final dataset split does not match requested stage")
            groups = {item.id: item.independence_group for item in dataset.cases}
            prepared = _PreparedDataset(
                source=source,
                sha256=stable_sha256(dataset.model_dump(mode="json")),
                cases=tuple(SearchCase(id=item.id) for item in dataset.cases),
                groups=groups,
                simulated=True,
                curated=False,
            )
            return prepared, dataset
        if not source.is_dir():
            raise FinalEvaluationError("process final dataset must be a DatasetVersion root")
        loaded = DatasetLoader().load(source)
        if loaded.manifest.demo_only and not spec.evaluator.simulated:
            raise FinalEvaluationError("non-simulated evaluator refuses a demo_only dataset")
        if any(item.metadata.split != expected_split for item in loaded.cases):
            raise FinalEvaluationError(f"dataset must contain {spec.stage} cases only")
        if loaded.dataset_version is None:
            raise FinalEvaluationError("process final input must be a published DatasetVersion")
        self._assert_disjoint_from_search(optimization_job_id, loaded)
        prepared = _PreparedDataset(
            source=source,
            sha256=loaded.dataset_sha256,
            cases=tuple(SearchCase(id=item.metadata.case_id) for item in loaded.cases),
            groups={
                item.metadata.case_id: item.metadata.group_keys.independence_group
                for item in loaded.cases
            },
            simulated=spec.evaluator.simulated,
            curated=True,
        )
        return prepared, None

    def _assert_disjoint_from_search(self, optimization_job_id: UUID, final: LoadedDataset) -> None:
        search_root = (
            self.optimization_store.job_dir(optimization_job_id)
            / "inputs"
            / "validation-dataset"
        )
        if not search_root.exists():
            raise FinalEvaluationError("optimization job lacks frozen validation_search dataset")
        search = DatasetLoader().load(search_root)
        if search.dataset_version is None:
            raise FinalEvaluationError("validation_search input is not a published DatasetVersion")
        assert final.dataset_version is not None
        try:
            require_common_split_plan_lineage(
                (search.dataset_version.metadata, final.dataset_version.metadata)
            )
        except SplitAuditError as exc:
            raise FinalEvaluationError(str(exc)) from exc
        fields = ("independence_group", "repository", "fork_lineage", "patch_family")
        for field in fields:
            search_values = {
                getattr(item.metadata.group_keys, field) for item in search.cases
            }
            final_values = {getattr(item.metadata.group_keys, field) for item in final.cases}
            if search_values & final_values:
                raise FinalEvaluationError(
                    f"final dataset overlaps validation_search by {field}"
                )

    def _freeze_dataset(self, prepared: _PreparedDataset, input_dir: Path) -> Path:
        if prepared.curated:
            target = input_dir / "final-dataset"
            shutil.copytree(prepared.source, target)
            if DatasetLoader().load(target).dataset_sha256 != prepared.sha256:
                raise FinalEvaluationError("copied final DatasetVersion hash mismatch")
            return target / "dataset.yaml"
        target = input_dir / "final-validation.json"
        self.store.writer.write(target, prepared.source.read_bytes())
        # The canonical hash is checked from the parsed model during each evaluation.
        return target

    def _paired_runs(
        self,
        job: FinalEvaluationJob,
        evaluator: CandidateEvaluator,
        prepared: _PreparedDataset,
        dataset_file: Path,
        base_path: Path,
        winner_path: Path,
        timeout_seconds: int,
    ) -> Tuple[Tuple[CandidateEvaluation, ...], Tuple[CandidateEvaluation, ...]]:
        base_results = []
        winner_results = []
        stage = (
            SearchEvaluationStage.VALIDATION_CONFIRM
            if job.stage == FinalEvaluationStage.VALIDATION_CONFIRM
            else SearchEvaluationStage.LOCKED_TEST
        )
        for repeat in range(job.repeats):
            order: Sequence[Tuple[str, Path]] = (
                ("base", base_path),
                ("winner", winner_path),
            )
            if repeat % 2:
                order = tuple(reversed(order))
            for variant, skill_path in order:
                evaluation = self._evaluate_once(
                    evaluator,
                    prepared,
                    dataset_file,
                    skill_path,
                    stage,
                    timeout_seconds,
                    job.base_skill_sha256 if variant == "base" else job.winner_skill_sha256,
                )
                if variant == "base":
                    base_results.append(evaluation)
                else:
                    winner_results.append(evaluation)
        return tuple(base_results), tuple(winner_results)

    def _evaluate_once(
        self,
        evaluator: CandidateEvaluator,
        prepared: _PreparedDataset,
        dataset_file: Path,
        skill_path: Path,
        stage: SearchEvaluationStage,
        timeout_seconds: int,
        expected_skill_sha256: str,
    ) -> CandidateEvaluation:
        self._assert_inputs(prepared, dataset_file, skill_path, expected_skill_sha256)
        try:
            return evaluator.evaluate(
                skill_path,
                dataset_file,
                prepared.sha256,
                prepared.cases,
                stage,
                timeout_seconds,
            )
        finally:
            self._assert_inputs(prepared, dataset_file, skill_path, expected_skill_sha256)

    @staticmethod
    def _assert_inputs(
        prepared: _PreparedDataset,
        dataset_file: Path,
        skill_path: Path,
        expected_skill_sha256: str,
    ) -> None:
        if _sha256(skill_path.read_bytes()) != expected_skill_sha256:
            raise FinalEvaluationError("frozen final-evaluation Skill integrity mismatch")
        if prepared.curated:
            actual = DatasetLoader().load(dataset_file.parent).dataset_sha256
        else:
            actual = stable_sha256(
                SimulatedFinalDataset.model_validate(
                    yaml.safe_load(dataset_file.read_text(encoding="utf-8"))
                ).model_dump(mode="json")
            )
        if actual != prepared.sha256:
            raise FinalEvaluationError("frozen final dataset integrity mismatch")

    @staticmethod
    def _comparisons(
        base: Sequence[CandidateEvaluation],
        winner: Sequence[CandidateEvaluation],
        groups: Mapping[str, str],
    ) -> Tuple[FinalCaseComparison, ...]:
        base_by_case = IndependentFinalEvaluator._case_values(base)
        winner_by_case = IndependentFinalEvaluator._case_values(winner)
        comparisons = []
        for case_id in base[0].case_ids:
            base_pass, base_score = base_by_case[case_id]
            winner_pass, winner_score = winner_by_case[case_id]
            if winner_pass > base_pass:
                classification = PairClassification.WIN
            elif winner_pass < base_pass:
                classification = PairClassification.LOSS
            elif winner_pass >= 0.5:
                classification = PairClassification.TIE_POSITIVE
            else:
                classification = PairClassification.TIE_NEGATIVE
            comparisons.append(
                FinalCaseComparison(
                    case_id=case_id,
                    independence_group=groups[case_id],
                    base_pass_rate=base_pass,
                    winner_pass_rate=winner_pass,
                    base_mean_score=base_score,
                    winner_mean_score=winner_score,
                    classification=classification,
                )
            )
        return tuple(comparisons)

    @staticmethod
    def _case_values(
        evaluations: Sequence[CandidateEvaluation],
    ) -> Dict[str, Tuple[float, float]]:
        values: Dict[str, list[SearchCaseResult]] = {}
        for evaluation in evaluations:
            for result in evaluation.results:
                values.setdefault(result.case_id, []).append(result)
        return {
            case_id: (
                sum(item.passed for item in rows) / len(rows),
                sum(item.score for item in rows) / len(rows),
            )
            for case_id, rows in values.items()
        }

    def _report(
        self,
        job: FinalEvaluationJob,
        spec: IndependentFinalEvaluationSpec,
        base: Tuple[CandidateEvaluation, ...],
        winner: Tuple[CandidateEvaluation, ...],
        comparisons: Tuple[FinalCaseComparison, ...],
    ) -> FinalEvaluationReport:
        base_pass, winner_pass = self._group_weighted_rates(comparisons)
        base_tokens = sum(item.total_tokens for item in base)
        winner_tokens = sum(item.total_tokens for item in winner)
        overhead = None if base_tokens == 0 else (winner_tokens - base_tokens) / base_tokens
        counts = {item: 0 for item in PairClassification}
        for comparison in comparisons:
            counts[comparison.classification] += 1
        groups = len({item.independence_group for item in comparisons})
        gain = winner_pass - base_pass
        gain_ci_low, gain_ci_high = self._gain_interval(
            comparisons,
            spec.gates.bootstrap_resamples,
            spec.gates.bootstrap_seed,
        )
        if counts[PairClassification.LOSS] > spec.gates.max_loss_cases:
            decision = FinalDecision.REGRESSION
            reason = "loss-case gate exceeded"
        elif overhead is not None and overhead > spec.gates.max_token_overhead_ratio:
            decision = FinalDecision.REGRESSION
            reason = "token-overhead gate exceeded"
        elif groups < spec.gates.min_independent_groups:
            decision = FinalDecision.DESCRIPTIVE_ONLY
            reason = "too few independent groups for confirmation"
        elif gain_ci_low >= spec.gates.min_absolute_gain:
            decision = FinalDecision.CONFIRMED
            reason = "all gates passed, including the group-bootstrap lower confidence bound"
        else:
            decision = FinalDecision.NOT_CONFIRMED
            reason = "minimum group-bootstrap gain bound was not met"
        completed = job.model_copy(
            update={
                "status": FinalEvaluationStatus.COMPLETED,
                "completed_at": _utcnow(),
                "decision": decision,
            }
        )
        if job.simulated:
            claim = "Engineering simulation only; this is not Agent performance evidence."
        elif job.stage == FinalEvaluationStage.VALIDATION_CONFIRM:
            claim = "Confirmation-split evidence only; locked-test performance remains unknown."
        else:
            claim = (
                "One-shot locked-test evidence for the exact frozen artifacts "
                "and DatasetVersion only."
            )
        return FinalEvaluationReport(
            job=completed,
            base_evaluations=base,
            winner_evaluations=winner,
            cases=comparisons,
            base_pass_rate=base_pass,
            winner_pass_rate=winner_pass,
            absolute_gain=gain,
            gain_ci_low=gain_ci_low,
            gain_ci_high=gain_ci_high,
            bootstrap_resamples=spec.gates.bootstrap_resamples,
            bootstrap_seed=spec.gates.bootstrap_seed,
            token_overhead_ratio=overhead,
            win_count=counts[PairClassification.WIN],
            tie_positive_count=counts[PairClassification.TIE_POSITIVE],
            tie_negative_count=counts[PairClassification.TIE_NEGATIVE],
            loss_count=counts[PairClassification.LOSS],
            independent_group_count=groups,
            decision=decision,
            decision_reason=reason,
            claim_limit=claim,
        )

    @staticmethod
    def _group_weighted_rates(
        comparisons: Sequence[FinalCaseComparison],
    ) -> Tuple[float, float]:
        grouped: Dict[str, list[FinalCaseComparison]] = {}
        for item in comparisons:
            grouped.setdefault(item.independence_group, []).append(item)
        base = [sum(item.base_pass_rate for item in rows) / len(rows) for rows in grouped.values()]
        winner = [
            sum(item.winner_pass_rate for item in rows) / len(rows) for rows in grouped.values()
        ]
        return sum(base) / len(base), sum(winner) / len(winner)

    @staticmethod
    def _gain_interval(
        comparisons: Sequence[FinalCaseComparison], resamples: int, seed: int
    ) -> Tuple[float, float]:
        grouped: Dict[str, list[float]] = {}
        for item in comparisons:
            grouped.setdefault(item.independence_group, []).append(
                item.winner_pass_rate - item.base_pass_rate
            )
        effects = [sum(values) / len(values) for values in grouped.values()]
        rng = random.Random(seed)
        draws = sorted(
            sum(rng.choice(effects) for _ in effects) / len(effects) for _ in range(resamples)
        )
        low_index = int(0.025 * (resamples - 1))
        high_index = int(0.975 * (resamples - 1))
        return draws[low_index], draws[high_index]

    def _load_completed(self, job_id: UUID) -> FinalEvaluationResult:
        job = self.store.load_job(job_id)
        if job.status != FinalEvaluationStatus.COMPLETED:
            raise FinalEvaluationError("existing final-evaluation job is not completed")
        report = self.store.load_report(job_id)
        job_dir = self.store.job_dir(job_id)
        self._assert_frozen_job_inputs(job, job_dir)
        return FinalEvaluationResult(
            job,
            report,
            job_dir / "reports" / "final-report.json",
            job_dir / "reports" / "final-report.html",
        )

    @staticmethod
    def _assert_frozen_job_inputs(job: FinalEvaluationJob, job_dir: Path) -> None:
        if _sha256((job_dir / "inputs" / "base-SKILL.md").read_bytes()) != job.base_skill_sha256:
            raise FinalEvaluationError("persisted base Skill integrity mismatch")
        if (
            _sha256((job_dir / "inputs" / "winner-SKILL.md").read_bytes())
            != job.winner_skill_sha256
        ):
            raise FinalEvaluationError("persisted winner Skill integrity mismatch")
        curated = job_dir / "inputs" / "final-dataset"
        if curated.exists():
            dataset_sha256 = DatasetLoader().load(curated).dataset_sha256
        else:
            payload = yaml.safe_load(
                (job_dir / "inputs" / "final-validation.json").read_text(encoding="utf-8")
            )
            dataset_sha256 = stable_sha256(
                SimulatedFinalDataset.model_validate(payload).model_dump(mode="json")
            )
        if dataset_sha256 != job.dataset_sha256:
            raise FinalEvaluationError("persisted final dataset integrity mismatch")

    @staticmethod
    def _one_candidate(
        candidates: Sequence[SkillCandidate], origin: CandidateOrigin
    ) -> SkillCandidate:
        matches = [item for item in candidates if item.origin == origin]
        if len(matches) != 1:
            raise FinalEvaluationError(f"expected exactly one {origin.value} candidate")
        return matches[0]
