"""Service-free successive-halving and Pareto search for Markdown Agent Skills."""

from __future__ import annotations

import hashlib
import html
import json
import random
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

from agentskill_eval_benchmark_gen import DatasetLoader, DatasetSplit
from agentskill_eval_contracts import (
    CandidateEvaluation,
    CandidateOrigin,
    OptimizationJob,
    OptimizationJobStatus,
    SearchEvaluationStage,
    SkillCandidate,
    SkillCandidateStatus,
    SkillCandidateTransition,
    SkillLintResult,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes
from agentskill_eval_skill_optimizer.evaluator import CandidateEvaluator, build_evaluator
from agentskill_eval_skill_optimizer.real_evaluator import (
    RealAgentCandidateEvaluator,
    RealEvaluationAuthorization,
)
from agentskill_eval_skill_optimizer.spec import (
    MutationSpec,
    OptimizationSearchSpec,
    SearchCase,
    ValidationSearchDataset,
)


class SkillSearchError(RuntimeError):
    """Raised when leakage, budget, integrity, or selection constraints fail."""


@dataclass(frozen=True)
class SkillSearchResult:
    job: OptimizationJob
    candidates: Tuple[SkillCandidate, ...]
    winner: SkillCandidate
    report_json: Path
    report_html: Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class OptimizationStore:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "optimization-jobs"
        self.writer = AtomicFileWriter()

    def job_dir(self, job_id: UUID) -> Path:
        return self.root / str(job_id)

    def save_job(self, job: OptimizationJob) -> None:
        self.writer.write(self.job_dir(job.id) / "job.json", model_bytes(job))

    def load_job(self, job_id: UUID) -> OptimizationJob:
        return load_model((self.job_dir(job_id) / "job.json").read_bytes(), OptimizationJob)

    def save_candidate(self, candidate: SkillCandidate) -> None:
        directory = self.job_dir(candidate.job_id) / "candidates" / str(candidate.id)
        snapshot = directory / "history" / f"{len(candidate.transitions):04d}.json"
        if snapshot.exists():
            raise SkillSearchError(f"immutable candidate snapshot exists: {snapshot}")
        content = model_bytes(candidate)
        self.writer.write(snapshot, content)
        self.writer.write(directory / "candidate.json", content)

    def load_candidate(self, job_id: UUID, candidate_id: UUID) -> SkillCandidate:
        path = self.job_dir(job_id) / "candidates" / str(candidate_id) / "candidate.json"
        return load_model(path.read_bytes(), SkillCandidate)

    def list_candidates(self, job: OptimizationJob) -> Tuple[SkillCandidate, ...]:
        return tuple(self.load_candidate(job.id, item) for item in job.candidate_ids)

    def skill_path(self, candidate: SkillCandidate) -> Path:
        return self.job_dir(candidate.job_id) / "candidates" / str(candidate.id) / "SKILL.md"

    def save_skill(self, candidate: SkillCandidate, content: bytes) -> None:
        if _sha256(content) != candidate.content_sha256:
            raise SkillSearchError("candidate content does not match declared hash")
        target = self.skill_path(candidate)
        if target.exists():
            raise SkillSearchError("candidate Skill content is immutable")
        self.writer.write(target, content)

    def assert_skill_integrity(self, candidate: SkillCandidate) -> None:
        if _sha256(self.skill_path(candidate).read_bytes()) != candidate.content_sha256:
            raise SkillSearchError("candidate Skill integrity mismatch")


class BenchmarkGuidedSkillSearch:
    """Generate candidates, halve on validation subsets, and freeze one Pareto winner."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.store = OptimizationStore(workspace)

    def run(
        self,
        spec: OptimizationSearchSpec,
        *,
        real_authorization: Optional[RealEvaluationAuthorization] = None,
    ) -> SkillSearchResult:
        base_content = self._read_markdown_skill(spec.base_skill_path)
        manual_content = self._read_markdown_skill(spec.manual_skill_path)
        validation_source = spec.validation_search_path.resolve(strict=True)
        curated_source: Optional[Path] = None
        if spec.evaluator.type == "simulated_keyword":
            if (
                validation_source.name != "validation-search.yaml"
                or not validation_source.is_file()
            ):
                raise SkillSearchError(
                    "simulated validation_search_path must name validation-search.yaml"
                )
            dataset = ValidationSearchDataset.load(validation_source)
            if not dataset.simulated:
                raise SkillSearchError("simulated evaluator requires a simulated dataset")
            dataset_sha = stable_sha256(dataset.model_dump(mode="json"))
        else:
            if not validation_source.is_dir():
                raise SkillSearchError(
                    "process validation_search_path must be a DatasetVersion root"
                )
            loaded = DatasetLoader().load(validation_source)
            if loaded.manifest.demo_only and not spec.evaluator.simulated:
                raise SkillSearchError(
                    "non-simulated process evaluator refuses a demo_only dataset"
                )
            if any(item.metadata.split != DatasetSplit.VALIDATION_SEARCH for item in loaded.cases):
                raise SkillSearchError(
                    "process search dataset must contain validation_search cases only"
                )
            dataset = ValidationSearchDataset(
                schema_version="ase/optimizer-validation/v1alpha1",
                name=loaded.manifest.name,
                version=loaded.manifest.version,
                split="validation_search",
                simulated=False,
                cases=tuple(SearchCase(id=item.metadata.case_id) for item in loaded.cases),
                source_dataset=f"{loaded.manifest.name}@{loaded.manifest.version}",
            )
            dataset_sha = loaded.dataset_sha256
            curated_source = validation_source
        evaluator = build_evaluator(
            spec.evaluator,
            dataset,
            workspace=self.workspace,
            real_authorization=real_authorization,
            baseline_skill_path=spec.base_skill_path,
        )
        semantic_spec = spec.model_dump(
            mode="json",
            exclude={"base_skill_path", "manual_skill_path", "validation_search_path"},
        )
        evaluator_spec = semantic_spec.get("evaluator")
        if isinstance(evaluator_spec, dict) and evaluator_spec.get(
            "real_agent_config_path"
        ) is None:
            evaluator_spec.pop("real_agent_config_path", None)
        spec_sha = stable_sha256(semantic_spec)
        identity = {
            "spec": spec_sha,
            "base": _sha256(base_content),
            "manual": _sha256(manual_content),
            "dataset": dataset_sha,
            "evaluator": evaluator.evaluator_sha256,
        }
        job_id = uuid5(NAMESPACE_URL, f"ase-optimization:{stable_sha256(identity)}")
        if self.store.job_dir(job_id).exists():
            return self._load_completed(job_id)
        definitions = self._candidate_definitions(spec, base_content, manual_content)
        if isinstance(evaluator, RealAgentCandidateEvaluator):
            if spec.search.subset_size % 2 or (
                len(dataset.cases) - spec.search.subset_size
            ) % 2:
                raise SkillSearchError(
                    "real Agent search requires even subset and remaining Case counts"
                )
            unique_candidate_cases = (
                len(definitions) * spec.search.subset_size
                + (3 + spec.search.promote_search_candidates)
                * (len(dataset.cases) - spec.search.subset_size)
            )
            evaluator.authorize_plan(unique_candidate_cases)
        candidate_ids = tuple(
            uuid5(job_id, f"candidate:{origin.value}:{name}:{_sha256(content)}")
            for name, origin, _parent, _mutations, content in definitions
        )
        required_budget = (
            len(definitions) * spec.search.subset_size
            + (3 + spec.search.promote_search_candidates) * len(dataset.cases)
        )
        if spec.budget.max_candidate_case_evaluations < required_budget:
            raise SkillSearchError(
                f"budget {spec.budget.max_candidate_case_evaluations} is below worst-case "
                f"successive-halving requirement {required_budget}"
            )
        job = OptimizationJob(
            id=job_id,
            status=OptimizationJobStatus.SEARCHING,
            spec_sha256=spec_sha,
            base_skill_sha256=_sha256(base_content),
            validation_dataset_sha256=dataset_sha,
            evaluator_sha256=evaluator.evaluator_sha256,
            candidate_ids=candidate_ids,
            evaluation_budget=spec.budget.max_candidate_case_evaluations,
            evaluations_used=0,
            simulated=evaluator.simulated,
            created_at=_utcnow(),
            metadata={
                "algorithm": spec.search.algorithm,
                "claims": "engineering_simulation" if evaluator.simulated else "validation_only",
                "locked_test_policy": "not_accessible_to_search_process",
            },
        )
        self.store.save_job(job)
        job_dir = self.store.job_dir(job.id)
        self.store.writer.write(job_dir / "search-spec.json", canonical_json(semantic_spec))
        self.store.writer.write(
            job_dir / "validation-search.json",
            canonical_json(dataset.model_dump(mode="json")),
        )
        inputs = job_dir / "inputs"
        self.store.writer.write(inputs / "base-SKILL.md", base_content)
        self.store.writer.write(inputs / "manual-SKILL.md", manual_content)
        if curated_source is None:
            frozen_dataset_file = job_dir / "validation-search.json"
        else:
            frozen_dataset_root = inputs / "validation-dataset"
            shutil.copytree(curated_source, frozen_dataset_root)
            if DatasetLoader().load(frozen_dataset_root).dataset_sha256 != dataset_sha:
                raise SkillSearchError("copied validation DatasetVersion hash mismatch")
            frozen_dataset_file = frozen_dataset_root / "dataset.yaml"
        candidates = []
        parent_id = candidate_ids[0]
        for candidate_id, definition in zip(candidate_ids, definitions):
            name, origin, parent, mutation_ids, content = definition
            candidate = self._new_candidate(
                job,
                candidate_id,
                name,
                origin,
                None if parent is None else parent_id,
                mutation_ids,
                content,
            )
            self.store.save_candidate(candidate)
            self.store.save_skill(candidate, content)
            candidate = self._lint(spec, dataset, candidate)
            self.store.save_candidate(candidate)
            candidates.append(candidate)
        subset = self._subset(dataset.cases, spec.search.subset_size, spec.search.random_seed)
        screened = []
        for candidate in candidates:
            if candidate.status == SkillCandidateStatus.REJECTED:
                screened.append(candidate)
                continue
            evaluation = self._evaluate_candidate(
                evaluator,
                candidate,
                frozen_dataset_file,
                dataset_sha,
                subset,
                SearchEvaluationStage.SUBSET,
                spec.budget.timeout_seconds,
                curated=curated_source is not None,
            )
            job = self._consume_budget(job, len(subset))
            candidate = self._transition(
                candidate,
                SkillCandidateStatus.SCREENED,
                "search:subset-evaluator",
                {"evaluation": evaluation.model_dump(mode="json")},
                {"evaluations": (*candidate.evaluations, evaluation)},
            )
            self.store.save_candidate(candidate)
            screened.append(candidate)
        promoted_ids = self._promotion_ids(screened, spec.search.promote_search_candidates)
        promoted = []
        for candidate in screened:
            if candidate.status != SkillCandidateStatus.SCREENED:
                promoted.append(candidate)
                continue
            if candidate.id in promoted_ids:
                candidate = self._transition(
                    candidate,
                    SkillCandidateStatus.PROMOTED,
                    "search:successive-halving",
                    {"promoted": True},
                )
            else:
                candidate = self._transition(
                    candidate,
                    SkillCandidateStatus.ELIMINATED,
                    "search:successive-halving",
                    {"promoted": False},
                    {"elimination_reason": "eliminated after validation subset"},
                )
            self.store.save_candidate(candidate)
            promoted.append(candidate)
        job = job.model_copy(update={"status": OptimizationJobStatus.VALIDATING})
        self.store.save_job(job)
        full_validated = []
        for candidate in promoted:
            if candidate.status != SkillCandidateStatus.PROMOTED:
                full_validated.append(candidate)
                continue
            evaluation = self._evaluate_candidate(
                evaluator,
                candidate,
                frozen_dataset_file,
                dataset_sha,
                dataset.cases,
                SearchEvaluationStage.FULL,
                spec.budget.timeout_seconds,
                curated=curated_source is not None,
            )
            job = self._consume_budget(job, len(dataset.cases))
            candidate = self._transition(
                candidate,
                SkillCandidateStatus.FULL_VALIDATED,
                "search:full-validation",
                {"evaluation": evaluation.model_dump(mode="json")},
                {"evaluations": (*candidate.evaluations, evaluation)},
            )
            self.store.save_candidate(candidate)
            full_validated.append(candidate)
        try:
            winner, dominated = self._select_winner(full_validated, spec)
        except SkillSearchError:
            self.store.save_job(job.model_copy(update={"status": OptimizationJobStatus.FAILED}))
            raise
        finalized = []
        for candidate in full_validated:
            if candidate.status != SkillCandidateStatus.FULL_VALIDATED:
                finalized.append(candidate)
                continue
            domination = tuple(dominated.get(candidate.id, ()))
            if candidate.id == winner.id:
                candidate = self._transition(
                    candidate,
                    SkillCandidateStatus.FROZEN,
                    "search:freeze-winner",
                    {"winner": True, "dominated_by": []},
                    {"pareto_dominated_by": domination},
                )
            else:
                reason = (
                    "Pareto dominated after full validation"
                    if domination
                    else "not selected; winner is restricted to search-origin candidates"
                )
                candidate = self._transition(
                    candidate,
                    SkillCandidateStatus.ELIMINATED,
                    "search:pareto-selection",
                    {"winner": False, "dominated_by": [str(item) for item in domination]},
                    {"pareto_dominated_by": domination, "elimination_reason": reason},
                )
            self.store.save_candidate(candidate)
            finalized.append(candidate)
        winner = next(item for item in finalized if item.id == winner.id)
        job = job.model_copy(
            update={
                "status": OptimizationJobStatus.FROZEN,
                "evaluations_used": job.evaluations_used,
                "frozen_winner_id": winner.id,
                "completed_at": _utcnow(),
            }
        )
        self.store.save_job(job)
        report_json, report_html = self._write_report(job, finalized, winner)
        return SkillSearchResult(job, tuple(finalized), winner, report_json, report_html)

    def _load_completed(self, job_id: UUID) -> SkillSearchResult:
        job = self.store.load_job(job_id)
        if job.status != OptimizationJobStatus.FROZEN or job.frozen_winner_id is None:
            raise SkillSearchError("existing optimization job is not safely resumable")
        candidates = self.store.list_candidates(job)
        for candidate in candidates:
            self.store.assert_skill_integrity(candidate)
        winner = next(item for item in candidates if item.id == job.frozen_winner_id)
        report_dir = self.store.job_dir(job.id) / "reports"
        return SkillSearchResult(
            job,
            candidates,
            winner,
            report_dir / "search-report.json",
            report_dir / "search-report.html",
        )

    @staticmethod
    def _read_markdown_skill(path: Path) -> bytes:
        resolved = path.resolve(strict=True)
        if resolved.is_dir():
            for child in resolved.rglob("*"):
                if child.is_symlink():
                    raise SkillSearchError("Skill source may not contain symlinks")
                if child.is_file() and child.relative_to(resolved).as_posix() not in {
                    "SKILL.md",
                    "metadata.yaml",
                }:
                    raise SkillSearchError(
                        "search MVP accepts only SKILL.md plus optional metadata.yaml"
                    )
            resolved = resolved / "SKILL.md"
        if not resolved.is_file() or resolved.is_symlink() or resolved.name != "SKILL.md":
            raise SkillSearchError("Skill source must resolve to a regular SKILL.md")
        content = resolved.read_bytes()
        if not content.strip():
            raise SkillSearchError("SKILL.md must not be empty")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillSearchError("SKILL.md must be UTF-8") from exc
        return content

    @staticmethod
    def _candidate_definitions(
        spec: OptimizationSearchSpec, base: bytes, manual: bytes
    ) -> Tuple[Tuple[str, CandidateOrigin, Optional[str], Tuple[str, ...], bytes], ...]:
        rng = random.Random(spec.search.random_seed)
        random_source = rng.choice(spec.mutations)
        definitions = [
            ("original", CandidateOrigin.ORIGINAL, None, (), base),
            ("manual", CandidateOrigin.MANUAL, "base", ("manual",), manual),
            (
                "random",
                CandidateOrigin.RANDOM,
                "base",
                (f"randomized-{random_source.id}",),
                BenchmarkGuidedSkillSearch._random_mutate(base, random_source, rng),
            ),
        ]
        definitions.extend(
            (
                f"search-{mutation.id}",
                CandidateOrigin.SEARCH,
                "base",
                (mutation.id,),
                BenchmarkGuidedSkillSearch._mutate(base, (mutation,)),
            )
            for mutation in spec.mutations
        )
        return tuple(definitions)

    @staticmethod
    def _mutate(base: bytes, mutations: Sequence[MutationSpec]) -> bytes:
        text = base.decode("utf-8").rstrip()
        blocks = ["\n\n## Candidate guidance"]
        for mutation in mutations:
            blocks.append(
                f"\n\n<!-- mutation:{mutation.id}; hypothesis:{mutation.hypothesis} -->\n"
                f"- {mutation.instruction.strip()}"
            )
        return (text + "".join(blocks) + "\n").encode("utf-8")

    @staticmethod
    def _random_mutate(base: bytes, mutation: MutationSpec, rng: random.Random) -> bytes:
        words = mutation.instruction.rstrip(".").split()
        rng.shuffle(words)
        randomized = " ".join(words) + "."
        surrogate = MutationSpec(
            id=f"randomized-{mutation.id}",
            hypothesis="Equal-size random word-order mutation comparator.",
            instruction=randomized,
        )
        return BenchmarkGuidedSkillSearch._mutate(base, (surrogate,))

    @staticmethod
    def _verified_dataset_file(path: Path, expected_sha256: str, *, curated: bool) -> Path:
        actual = (
            DatasetLoader().load(path.parent).dataset_sha256
            if curated
            else _sha256(path.read_bytes())
        )
        if actual != expected_sha256:
            raise SkillSearchError("frozen validation dataset integrity mismatch")
        return path

    def _evaluate_candidate(
        self,
        evaluator: CandidateEvaluator,
        candidate: SkillCandidate,
        dataset_file: Path,
        dataset_sha256: str,
        cases: Sequence[SearchCase],
        stage: SearchEvaluationStage,
        timeout_seconds: int,
        *,
        curated: bool,
    ) -> CandidateEvaluation:
        """Evaluate only while the frozen Skill and validation dataset remain unchanged."""
        self.store.assert_skill_integrity(candidate)
        verified_dataset = self._verified_dataset_file(
            dataset_file, dataset_sha256, curated=curated
        )
        try:
            return evaluator.evaluate(
                self.store.skill_path(candidate),
                verified_dataset,
                dataset_sha256,
                cases,
                stage,
                timeout_seconds,
            )
        finally:
            # The process adapter is an integration boundary, not a trusted writer.
            # Re-hash both inputs even when it fails so mutations cannot be scored.
            self.store.assert_skill_integrity(candidate)
            self._verified_dataset_file(dataset_file, dataset_sha256, curated=curated)

    def _new_candidate(
        self,
        job: OptimizationJob,
        candidate_id: UUID,
        name: str,
        origin: CandidateOrigin,
        parent_id: Optional[UUID],
        mutation_ids: Tuple[str, ...],
        content: bytes,
    ) -> SkillCandidate:
        transition = SkillCandidateTransition(
            sequence=1,
            from_status=None,
            to_status=SkillCandidateStatus.CREATED,
            occurred_at=_utcnow(),
            actor="search:candidate-generator",
            input_sha256=job.spec_sha256,
            output_sha256=_sha256(content),
        )
        return SkillCandidate(
            id=candidate_id,
            job_id=job.id,
            name=name,
            origin=origin,
            parent_id=parent_id,
            mutation_ids=mutation_ids,
            content_sha256=_sha256(content),
            content_bytes=len(content),
            status=SkillCandidateStatus.CREATED,
            transitions=(transition,),
        )

    def _lint(
        self,
        spec: OptimizationSearchSpec,
        dataset: ValidationSearchDataset,
        candidate: SkillCandidate,
    ) -> SkillCandidate:
        self.store.assert_skill_integrity(candidate)
        content = self.store.skill_path(candidate).read_text(encoding="utf-8")
        lowered = content.lower()
        forbidden = tuple(
            value.lower()
            for case in dataset.cases
            for value in (case.id, *case.leakage_tokens)
            if value
        )
        lint = (
            SkillLintResult(
                name="size_limit",
                passed=candidate.content_bytes <= spec.constraints.max_skill_bytes,
                detail=f"{candidate.content_bytes}/{spec.constraints.max_skill_bytes} bytes",
            ),
            SkillLintResult(
                name="benchmark_identifier_leakage",
                passed=not any(value in lowered for value in forbidden),
                detail="candidate contains no case IDs or registered answer/test leakage tokens",
            ),
            SkillLintResult(
                name="script_free_markdown",
                passed=True,
                detail="candidate is one immutable UTF-8 SKILL.md with no executable payload",
            ),
        )
        if not all(item.passed for item in lint):
            return self._transition(
                candidate,
                SkillCandidateStatus.REJECTED,
                "search:static-lint",
                {"lint": [item.model_dump() for item in lint]},
                {"lint_results": lint, "elimination_reason": "static lint failed"},
            )
        return self._transition(
            candidate,
            SkillCandidateStatus.LINTED,
            "search:static-lint",
            {"lint": [item.model_dump() for item in lint]},
            {"lint_results": lint},
        )

    @staticmethod
    def _subset(
        cases: Tuple[SearchCase, ...], size: int, seed: int
    ) -> Tuple[SearchCase, ...]:
        if size > len(cases):
            raise SkillSearchError("subset_size exceeds validation_search case count")
        selected = list(cases)
        random.Random(seed).shuffle(selected)
        return tuple(sorted(selected[:size], key=lambda item: item.id))

    @staticmethod
    def _promotion_ids(candidates: Sequence[SkillCandidate], width: int) -> set[UUID]:
        comparators = {
            item.id
            for item in candidates
            if item.status == SkillCandidateStatus.SCREENED
            and item.origin
            in {CandidateOrigin.ORIGINAL, CandidateOrigin.MANUAL, CandidateOrigin.RANDOM}
        }
        search = [
            item
            for item in candidates
            if item.status == SkillCandidateStatus.SCREENED
            and item.origin == CandidateOrigin.SEARCH
        ]
        search.sort(key=BenchmarkGuidedSkillSearch._ranking_key)
        return comparators | {item.id for item in search[:width]}

    @staticmethod
    def _ranking_key(candidate: SkillCandidate) -> Tuple[float, float, int, int]:
        evaluation = candidate.evaluations[-1]
        return (
            -evaluation.pass_rate,
            -evaluation.mean_score,
            evaluation.total_tokens,
            candidate.content_bytes,
        )

    @staticmethod
    def _full(candidate: SkillCandidate) -> CandidateEvaluation:
        return next(
            item for item in candidate.evaluations if item.stage == SearchEvaluationStage.FULL
        )

    def _select_winner(
        self, candidates: Sequence[SkillCandidate], spec: OptimizationSearchSpec
    ) -> Tuple[SkillCandidate, Dict[UUID, Tuple[UUID, ...]]]:
        full = [item for item in candidates if item.status == SkillCandidateStatus.FULL_VALIDATED]
        original = next(item for item in full if item.origin == CandidateOrigin.ORIGINAL)
        base_eval = self._full(original)
        dominated: Dict[UUID, Tuple[UUID, ...]] = {}
        for candidate in full:
            dominators = tuple(
                other.id
                for other in full
                if other.id != candidate.id and self._dominates(other, candidate)
            )
            dominated[candidate.id] = dominators
        feasible = []
        base_by_case = {item.case_id: item for item in base_eval.results}
        for candidate in full:
            if candidate.origin != CandidateOrigin.SEARCH or dominated[candidate.id]:
                continue
            evaluation = self._full(candidate)
            losses = sum(
                base_by_case[result.case_id].passed and not result.passed
                for result in evaluation.results
            )
            token_overhead = (evaluation.total_tokens - base_eval.total_tokens) / max(
                1, base_eval.total_tokens
            )
            if losses <= spec.constraints.max_loss_cases and (
                token_overhead <= spec.constraints.max_token_overhead_ratio
            ):
                feasible.append(candidate)
        if not feasible:
            raise SkillSearchError(
                "no search-origin candidate satisfies Pareto and hard constraints"
            )
        feasible.sort(key=self._ranking_key)
        return feasible[0], dominated

    def _dominates(self, left: SkillCandidate, right: SkillCandidate) -> bool:
        a, b = self._full(left), self._full(right)
        no_worse = (
            a.pass_rate >= b.pass_rate
            and a.mean_score >= b.mean_score
            and a.total_tokens <= b.total_tokens
            and a.total_latency_ms <= b.total_latency_ms
            and left.content_bytes <= right.content_bytes
        )
        strictly_better = (
            a.pass_rate > b.pass_rate
            or a.mean_score > b.mean_score
            or a.total_tokens < b.total_tokens
            or a.total_latency_ms < b.total_latency_ms
            or left.content_bytes < right.content_bytes
        )
        return no_worse and strictly_better

    def _consume_budget(self, job: OptimizationJob, amount: int) -> OptimizationJob:
        used = job.evaluations_used + amount
        if used > job.evaluation_budget:
            failed = job.model_copy(update={"status": OptimizationJobStatus.BUDGET_EXHAUSTED})
            self.store.save_job(failed)
            raise SkillSearchError("candidate-case evaluation budget exhausted")
        updated = job.model_copy(update={"evaluations_used": used})
        self.store.save_job(updated)
        return updated

    @staticmethod
    def _transition(
        candidate: SkillCandidate,
        status: SkillCandidateStatus,
        actor: str,
        output: Mapping[str, object],
        updates: Optional[Mapping[str, object]] = None,
    ) -> SkillCandidate:
        transition = SkillCandidateTransition(
            sequence=len(candidate.transitions) + 1,
            from_status=candidate.status,
            to_status=status,
            occurred_at=_utcnow(),
            actor=actor,
            input_sha256=stable_sha256(candidate.model_dump(mode="json", round_trip=True)),
            output_sha256=stable_sha256(output),
        )
        values: Dict[str, object] = {
            "status": status,
            "transitions": (*candidate.transitions, transition),
        }
        values.update(updates or {})
        return candidate.model_copy(update=values)

    def _write_report(
        self,
        job: OptimizationJob,
        candidates: Sequence[SkillCandidate],
        winner: SkillCandidate,
    ) -> Tuple[Path, Path]:
        report_dir = self.store.job_dir(job.id) / "reports"
        rows = []
        for candidate in candidates:
            subset = next(
                (
                    item
                    for item in candidate.evaluations
                    if item.stage == SearchEvaluationStage.SUBSET
                ),
                None,
            )
            full = next(
                (
                    item
                    for item in candidate.evaluations
                    if item.stage == SearchEvaluationStage.FULL
                ),
                None,
            )
            rows.append(
                {
                    "candidate_id": str(candidate.id),
                    "name": candidate.name,
                    "origin": candidate.origin.value,
                    "status": candidate.status.value,
                    "mutation_ids": list(candidate.mutation_ids),
                    "content_sha256": candidate.content_sha256,
                    "content_bytes": candidate.content_bytes,
                    "subset_pass_rate": None if subset is None else subset.pass_rate,
                    "subset_mean_score": None if subset is None else subset.mean_score,
                    "full_pass_rate": None if full is None else full.pass_rate,
                    "full_mean_score": None if full is None else full.mean_score,
                    "full_tokens": None if full is None else full.total_tokens,
                    "full_latency_ms": None if full is None else full.total_latency_ms,
                    "full_cost_microusd": (
                        None if full is None else full.total_cost_microusd
                    ),
                    "invalid_cases": (
                        []
                        if full is None
                        else [
                            item.case_id for item in full.results if item.outcome == "invalid"
                        ]
                    ),
                    "evaluations": [
                        item.model_dump(mode="json") for item in candidate.evaluations
                    ],
                    "pareto_dominated_by": [str(item) for item in candidate.pareto_dominated_by],
                    "elimination_reason": candidate.elimination_reason,
                }
            )
        report = {
            "schema_version": "ase/optimization-report/v1alpha1",
            "job": job.model_dump(mode="json"),
            "simulated": job.simulated,
            "claim_limit": (
                "controller engineering evidence only; not Agent performance evidence"
                if job.simulated
                else "adaptive validation result; not locked-test confirmation"
            ),
            "locked_test_accessed": False,
            "winner_id": str(winner.id),
            "candidates": rows,
            "group_coverage": sorted({item.origin.value for item in candidates}),
        }
        json_path = report_dir / "search-report.json"
        html_path = report_dir / "search-report.html"
        self.store.writer.write(
            json_path, json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
        )
        table_rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(row['name']))}</td>"
            f"<td>{html.escape(str(row['origin']))}</td>"
            f"<td>{html.escape(str(row['status']))}</td>"
            f"<td>{html.escape(str(row['full_pass_rate']))}</td>"
            f"<td>{html.escape(str(row['full_tokens']))}</td>"
            "</tr>"
            for row in rows
        )
        banner = (
            "SIMULATED — controller engineering evidence only"
            if job.simulated
            else "VALIDATION ONLY — adaptive search result, not confirmation"
        )
        page = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Skill Search</title>"
            "<style>body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}"
            "th,td{border:1px solid #bbb;padding:.5rem}.banner{padding:1rem;background:#ffe8a3}"
            "</style></head><body>"
            f"<div class='banner'>{html.escape(banner)}</div>"
            f"<h1>{html.escape(str(job.id))}</h1><p>Winner: {html.escape(winner.name)}</p>"
            "<table><thead><tr><th>Name</th><th>Group</th><th>Status</th>"
            f"<th>Pass rate</th><th>Tokens</th></tr></thead><tbody>{table_rows}</tbody></table>"
            "</body></html>"
        )
        self.store.writer.write(html_path, page.encode("utf-8"))
        return json_path, html_path
