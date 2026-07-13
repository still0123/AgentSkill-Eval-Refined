"""Auditable local Git-history benchmark reconstruction pipeline."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml

from agentskill_eval_benchmark_gen.git_source import GitSource, GitSourceError, GitTreeLimits
from agentskill_eval_benchmark_gen.spec import BenchmarkGenerationSpec, CandidateSpec
from agentskill_eval_contracts import (
    BenchmarkCandidate,
    BenchmarkCandidateStatus,
    BenchmarkDatasetVersion,
    BenchmarkJob,
    BenchmarkJobStatus,
    CandidateProvenance,
    CandidateTransition,
    CommandEvidence,
    PublishedCase,
    QualityGateResult,
    ReviewDecision,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes

GENERATOR_VERSION = "0.1.0"
VERIFIER_VERSION = "0.1.0"


class BenchmarkGenerationError(RuntimeError):
    """Raised when generation cannot safely continue."""


@dataclass(frozen=True)
class GenerationResult:
    job: BenchmarkJob
    candidates: Tuple[BenchmarkCandidate, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BenchmarkGenerationError(f"symlink is forbidden: {path}")
        if path.is_file():
            entries.append((path.relative_to(root).as_posix(), _file_sha256(path)))
    if not entries:
        raise BenchmarkGenerationError(f"empty tree: {root}")
    return stable_sha256(entries)


class BenchmarkStore:
    """Atomic manifest store with immutable per-transition candidate snapshots."""

    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "benchmark-jobs"
        self.writer = AtomicFileWriter()

    def job_dir(self, job_id: UUID) -> Path:
        return self.root / str(job_id)

    def save_job(self, job: BenchmarkJob) -> None:
        self.writer.write(self.job_dir(job.id) / "job.json", model_bytes(job))

    def load_job(self, job_id: UUID) -> BenchmarkJob:
        return load_model((self.job_dir(job_id) / "job.json").read_bytes(), BenchmarkJob)

    def save_candidate(self, candidate: BenchmarkCandidate) -> None:
        directory = self.job_dir(candidate.job_id) / "candidates" / str(candidate.id)
        snapshot = directory / "history" / f"{len(candidate.transitions):04d}.json"
        if snapshot.exists():
            raise BenchmarkGenerationError(f"immutable transition snapshot exists: {snapshot}")
        content = model_bytes(candidate)
        self.writer.write(snapshot, content)
        self.writer.write(directory / "candidate.json", content)

    def load_candidate(self, job_id: UUID, candidate_id: UUID) -> BenchmarkCandidate:
        path = self.job_dir(job_id) / "candidates" / str(candidate_id) / "candidate.json"
        return load_model(path.read_bytes(), BenchmarkCandidate)

    def list_candidates(self, job: BenchmarkJob) -> Tuple[BenchmarkCandidate, ...]:
        return tuple(
            self.load_candidate(job.id, candidate_id) for candidate_id in job.candidate_ids
        )

    def save_dataset_version(self, version: BenchmarkDatasetVersion, root: Path) -> Path:
        destination = self.root.parent / "dataset-versions" / str(version.id)
        if destination.exists():
            raise BenchmarkGenerationError("DatasetVersion is immutable and already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, destination)
        self.writer.write(destination / "dataset-version.json", model_bytes(version))
        return destination


class CommandVerifier:
    """Run controlled offline test commands and retain hashed evidence logs."""

    def __init__(self, store: BenchmarkStore, job_id: UUID, max_commands: int) -> None:
        self.store = store
        self.job_id = job_id
        self.max_commands = max_commands
        self.command_count = 0

    def run(
        self,
        candidate_id: UUID,
        variant: str,
        repeat: int,
        fixture: Path,
        command: Sequence[str],
        timeout: int,
    ) -> CommandEvidence:
        if self.command_count >= self.max_commands:
            raise BenchmarkGenerationError("command budget exhausted")
        self.command_count += 1
        argv = tuple(
            sys.executable if item in {"python", "python3", "${PYTHON}"} else item
            for item in command
        )
        runtime_dir = (
            self.store.job_dir(self.job_id)
            / "candidates"
            / str(candidate_id)
            / "runtime"
            / variant
            / f"{repeat:02d}"
        )
        runtime_dir.mkdir(parents=True, exist_ok=True)
        guard_dir = runtime_dir / "guard"
        guard_dir.mkdir()
        (guard_dir / "sitecustomize.py").write_text(
            "import socket\n"
            "def _deny(*args, **kwargs):\n"
            "    raise RuntimeError('network disabled by benchmark verifier')\n"
            "socket.create_connection = _deny\n"
            "socket.socket.connect = _deny\n",
            encoding="utf-8",
        )
        environment = {
            "HOME": str(runtime_dir / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "*",
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(guard_dir),
            "TZ": "UTC",
        }
        (runtime_dir / "home").mkdir()
        started = time.monotonic()
        timed_out = False
        exit_code: Optional[int]
        try:
            result = subprocess.run(
                argv,
                cwd=fixture,
                env=environment,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            exit_code = result.returncode
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
        duration_ms = int((time.monotonic() - started) * 1000)
        log_dir = (
            self.store.job_dir(self.job_id)
            / "candidates"
            / str(candidate_id)
            / "evidence"
            / variant
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"{repeat:02d}.stdout"
        stderr_path = log_dir / f"{repeat:02d}.stderr"
        self.store.writer.write(stdout_path, stdout)
        self.store.writer.write(stderr_path, stderr)
        return CommandEvidence(
            variant=variant,  # type: ignore[arg-type]
            repeat_index=repeat,
            argv=argv,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
        )


class AutomaticBenchmarkGenerator:
    """Reconstruct, verify, deduplicate, review, and publish candidates."""

    def __init__(self, workspace: Path) -> None:
        self.store = BenchmarkStore(workspace)

    def generate(self, spec: BenchmarkGenerationSpec) -> GenerationResult:
        semantic_spec = spec.model_dump(mode="json", exclude={"repository_path"})
        source_hash = stable_sha256(semantic_spec)
        job_id = uuid5(NAMESPACE_URL, f"ase-benchmark-job:{source_hash}")
        candidate_ids = tuple(uuid5(job_id, item.key) for item in spec.candidates)
        job = BenchmarkJob(
            id=job_id,
            status=BenchmarkJobStatus.RUNNING,
            source_spec_sha256=source_hash,
            generator_profile=spec.generator_profile,
            verifier_profile=spec.verifier_profile,
            target_split=spec.target_split,
            max_candidates=spec.budget.max_candidates,
            max_commands=spec.budget.max_commands,
            wall_seconds=spec.budget.wall_seconds,
            candidate_ids=candidate_ids,
            created_at=_utcnow(),
        )
        if self.store.job_dir(job_id).exists():
            existing = self.store.load_job(job_id)
            if existing.source_spec_sha256 != source_hash:
                raise BenchmarkGenerationError("existing job source hash mismatch")
            spec_path = self.store.job_dir(job_id) / "source-spec.json"
            if not spec_path.is_file() or hashlib.sha256(spec_path.read_bytes()).hexdigest() != (
                source_hash
            ):
                raise BenchmarkGenerationError("existing frozen source spec integrity mismatch")
            return GenerationResult(existing, self.store.list_candidates(existing))
        source = GitSource(spec.repository_path)
        if source.origin_url() != spec.repository_url:
            raise BenchmarkGenerationError("repository origin does not match pinned repository_url")
        self.store.save_job(job)
        self.store.writer.write(
            self.store.job_dir(job_id) / "source-spec.json", canonical_json(semantic_spec)
        )
        verifier = CommandVerifier(self.store, job_id, spec.budget.max_commands)
        started = time.monotonic()
        candidates = []
        seen: Dict[str, UUID] = {}
        for item, candidate_id in zip(spec.candidates, candidate_ids):
            if time.monotonic() - started > spec.budget.wall_seconds:
                job = job.model_copy(update={"status": BenchmarkJobStatus.BUDGET_EXHAUSTED})
                self.store.save_job(job)
                raise BenchmarkGenerationError("wall-clock budget exhausted")
            candidate = self._new_candidate(job, item, candidate_id)
            self.store.save_candidate(candidate)
            try:
                candidate = self._reconstruct(spec, source, item, candidate)
                self.store.save_candidate(candidate)
                candidate = self._verify(spec, item, candidate, verifier)
                self.store.save_candidate(candidate)
                candidate = self._deduplicate(candidate, seen)
                self.store.save_candidate(candidate)
            except (BenchmarkGenerationError, GitSourceError) as exc:
                candidate = self._reject(candidate, str(exc))
                self.store.save_candidate(candidate)
            candidates.append(candidate)
        completed = all(
            item.status in {BenchmarkCandidateStatus.DEDUPED, BenchmarkCandidateStatus.REJECTED}
            for item in candidates
        )
        job = job.model_copy(
            update={
                "status": BenchmarkJobStatus.COMPLETED if completed else BenchmarkJobStatus.PARTIAL,
                "completed_at": _utcnow(),
            }
        )
        self.store.save_job(job)
        return GenerationResult(job, tuple(candidates))

    def review(
        self, job_id: UUID, candidate_id: UUID, reviewer: str, decision: ReviewDecision, reason: str
    ) -> BenchmarkCandidate:
        if not reviewer.strip() or not reason.strip():
            raise BenchmarkGenerationError("reviewer and reason must be non-empty")
        candidate = self.store.load_candidate(job_id, candidate_id)
        if candidate.status != BenchmarkCandidateStatus.DEDUPED:
            raise BenchmarkGenerationError("only DEDUPED candidates may be reviewed")
        self._assert_candidate_integrity(candidate)
        if decision == ReviewDecision.APPROVED:
            candidate = self._transition(
                candidate,
                BenchmarkCandidateStatus.REVIEWED,
                actor=f"human:{reviewer}",
                output={"decision": decision.value, "reason": reason},
                updates={"review_decision": decision, "reviewer": reviewer},
            )
        else:
            candidate = self._transition(
                candidate,
                BenchmarkCandidateStatus.REJECTED,
                actor=f"human:{reviewer}",
                output={"decision": decision.value, "reason": reason},
                updates={
                    "review_decision": decision,
                    "reviewer": reviewer,
                    "rejection_reasons": (*candidate.rejection_reasons, reason),
                },
            )
        self.store.save_candidate(candidate)
        return candidate

    def publish(self, job_id: UUID, publisher: str) -> Tuple[BenchmarkDatasetVersion, Path]:
        job = self.store.load_job(job_id)
        candidates = tuple(
            item
            for item in self.store.list_candidates(job)
            if item.status == BenchmarkCandidateStatus.REVIEWED
        )
        if not candidates:
            raise BenchmarkGenerationError("no human-approved candidates to publish")
        lineages_by_split: Dict[str, str] = {}
        for item in candidates:
            assert item.provenance is not None
            self._assert_candidate_integrity(item)
            old_split = lineages_by_split.setdefault(
                item.provenance.fork_lineage, item.target_split
            )
            if old_split != item.target_split:
                raise BenchmarkGenerationError("fork lineage crosses dataset splits")
        staging = self.store.job_dir(job_id) / "publish-staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        published_cases = tuple(self._publish_case(candidate, staging) for candidate in candidates)
        content_sha = BenchmarkDatasetVersion.calculate_content_sha256(published_cases)
        version_id = uuid5(job_id, f"dataset:{content_sha}")
        version = BenchmarkDatasetVersion(
            id=version_id,
            name=f"generated-{job_id}",
            version=content_sha[:12],
            split=job.target_split,
            job_id=job.id,
            published_at=_utcnow(),
            publisher=publisher,
            cases=published_cases,
            content_sha256=content_sha,
            source_lineages=tuple(sorted(lineages_by_split)),
            metadata={"selection_uses_agent_scores": "false"},
        )
        self._write_dataset_manifest(staging, version, candidates)
        destination = self.store.save_dataset_version(version, staging)
        shutil.rmtree(staging)
        for candidate in candidates:
            updated = self._transition(
                candidate,
                BenchmarkCandidateStatus.PUBLISHED,
                actor=f"publisher:{publisher}",
                output={"dataset_version_id": str(version.id), "content_sha256": content_sha},
            )
            self.store.save_candidate(updated)
        return version, destination

    def _new_candidate(
        self, job: BenchmarkJob, spec: CandidateSpec, candidate_id: UUID
    ) -> BenchmarkCandidate:
        output = {"key": spec.key, "task": spec.task, "source": "git_history"}
        transition = CandidateTransition(
            sequence=1,
            from_status=None,
            to_status=BenchmarkCandidateStatus.INGESTED,
            occurred_at=_utcnow(),
            actor="generator:ingest",
            input_sha256=job.source_spec_sha256,
            output_sha256=stable_sha256(output),
        )
        return BenchmarkCandidate(
            id=candidate_id,
            job_id=job.id,
            key=spec.key,
            task=spec.task,
            target_split=job.target_split,
            status=BenchmarkCandidateStatus.INGESTED,
            transitions=(transition,),
        )

    def _reconstruct(
        self,
        spec: BenchmarkGenerationSpec,
        source: GitSource,
        item: CandidateSpec,
        candidate: BenchmarkCandidate,
    ) -> BenchmarkCandidate:
        before = source.resolve_commit(item.before_commit)
        after = source.resolve_commit(item.after_commit)
        source.assert_ancestor(before, after)
        base = self.store.job_dir(candidate.job_id) / "candidates" / str(candidate.id)
        fixture_root = base / "fixtures"
        limits = GitTreeLimits(
            spec.quality_gate.max_repository_files, spec.quality_gate.max_repository_bytes
        )
        before_dir, after_dir = fixture_root / "before", fixture_root / "after"
        mutation_dir, alternative_dir = fixture_root / "mutation", fixture_root / "alternative"
        source.materialize(before, before_dir, limits)
        source.materialize(after, after_dir, limits)
        for test_path in item.regression_test_paths:
            content = source.blob(after, test_path)
            target = before_dir / test_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        shutil.copytree(after_dir, mutation_dir)
        shutil.copytree(before_dir, alternative_dir)
        reference_patch = source.diff(before, after, item.production_paths)
        if not reference_patch:
            raise BenchmarkGenerationError("reference production patch is empty")
        source.apply_patch(mutation_dir, reference_patch, reverse=True)
        alternative_patch = self._normalize_unified_diff(item.alternative_patch)
        source.apply_patch(alternative_dir, alternative_patch)
        patch_dir = base / "evidence" / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)
        self.store.writer.write(patch_dir / "reference.patch", reference_patch)
        self.store.writer.write(patch_dir / "alternative.patch", alternative_patch)
        license_content = source.blob(after, spec.license_path)
        if not license_content.strip():
            raise BenchmarkGenerationError("license file is empty")
        provenance = CandidateProvenance(
            repository_url=spec.repository_url,
            fork_lineage=spec.fork_lineage,
            license_spdx=spec.license_spdx,
            license_sha256=hashlib.sha256(license_content).hexdigest(),
            before_commit=before,
            after_commit=after,
            after_committed_at=source.committed_at(after),
            issue_url=item.issue_url,
            reference_patch_sha256=hashlib.sha256(reference_patch).hexdigest(),
            generator_profile=spec.generator_profile,
            generator_version=GENERATOR_VERSION,
            verifier_profile=spec.verifier_profile,
            verifier_version=VERIFIER_VERSION,
            contamination_risk=spec.contamination_risk,
        )
        fixture_sha = _tree_sha256(before_dir)
        artifacts = {
            "fixture.before": fixture_sha,
            "fixture.after": _tree_sha256(after_dir),
            "fixture.mutation": _tree_sha256(mutation_dir),
            "fixture.alternative": _tree_sha256(alternative_dir),
            "patch.reference": hashlib.sha256(reference_patch).hexdigest(),
            "patch.alternative": hashlib.sha256(alternative_patch).hexdigest(),
        }
        return self._transition(
            candidate,
            BenchmarkCandidateStatus.RECONSTRUCTED,
            actor="generator:reconstruct",
            output={
                "before_fixture_sha256": fixture_sha,
                "after_fixture_sha256": artifacts["fixture.after"],
                "mutation_fixture_sha256": artifacts["fixture.mutation"],
                "alternative_fixture_sha256": artifacts["fixture.alternative"],
                "provenance": provenance.model_dump(mode="json"),
            },
            updates={
                "fixture_sha256": fixture_sha,
                "artifact_sha256": artifacts,
                "provenance": provenance,
            },
        )

    def _verify(
        self,
        spec: BenchmarkGenerationSpec,
        item: CandidateSpec,
        candidate: BenchmarkCandidate,
        verifier: CommandVerifier,
    ) -> BenchmarkCandidate:
        base = self.store.job_dir(candidate.job_id) / "candidates" / str(candidate.id)
        fixtures = base / "fixtures"
        gates = list(self._static_gates(spec, item, candidate, fixtures / "after"))
        evidence = []
        for variant, expected_success in (
            ("before", False),
            ("after", True),
            ("mutation", False),
            ("alternative", True),
        ):
            repeats = spec.quality_gate.repeat_count
            for repeat in range(1, repeats + 1):
                evidence.append(
                    verifier.run(
                        candidate.id,
                        variant,
                        repeat,
                        fixtures / variant,
                        item.test_command,
                        spec.quality_gate.timeout_seconds,
                    )
                )
            selected = evidence[-repeats:]
            passed = all(
                not run.timed_out
                and run.exit_code is not None
                and (run.exit_code == 0) == expected_success
                for run in selected
            )
            gates.append(
                QualityGateResult(
                    name=f"{variant}_stable_{'pass' if expected_success else 'fail'}",
                    passed=passed,
                    detail=f"{repeats} controlled offline repeats matched expected outcome",
                    evidence_sha256=stable_sha256(
                        [run.model_dump(mode="json") for run in selected]
                    ),
                )
            )
        failed = [gate.name for gate in gates if not gate.passed]
        if failed:
            raise BenchmarkGenerationError("quality gates failed: " + ", ".join(failed))
        oracle = {"command": list(item.test_command), "expected_exit_code": 0}
        grader = self._grader_text(item.test_command)
        return self._transition(
            candidate,
            BenchmarkCandidateStatus.VERIFIED,
            actor="verifier:deterministic",
            output={
                "quality_gates": [gate.model_dump(mode="json") for gate in gates],
                "command_evidence": [run.model_dump(mode="json") for run in evidence],
            },
            updates={
                "oracle_sha256": stable_sha256(oracle),
                "grader_sha256": hashlib.sha256(grader.encode()).hexdigest(),
                "quality_gates": tuple(gates),
                "command_evidence": tuple(evidence),
            },
        )

    def _static_gates(
        self,
        spec: BenchmarkGenerationSpec,
        item: CandidateSpec,
        candidate: BenchmarkCandidate,
        after_fixture: Path,
    ) -> Iterable[QualityGateResult]:
        selected_tests = self._selected_test_source(item, after_fixture)
        all_frozen_tests = "\n".join(
            (after_fixture / path).read_text(encoding="utf-8", errors="replace")
            for path in item.regression_test_paths
        )
        forbidden = re.compile(
            r"\b(requests|urllib|httpx|socket|aiohttp)\b|"
            r"\b(datetime\.(now|today)|time\.(time|sleep)|random\.)\b|"
            r"\b(subprocess|os\.system)\b"
        )
        deterministic = forbidden.search(selected_tests) is None
        yield QualityGateResult(
            name="offline_deterministic_test",
            passed=deterministic,
            detail=(
                "regression tests contain no network, wall-clock, sleep, or unseeded random APIs"
            ),
        )
        patch_path = (
            self.store.job_dir(candidate.job_id)
            / "candidates"
            / str(candidate.id)
            / "evidence"
            / "patches"
            / "reference.patch"
        )
        added = []
        for line in patch_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                value = line[1:].strip()
                if len(value) >= 16 and not value.startswith(("#", '"""')):
                    added.append(value)
        leaked = any(value in all_frozen_tests or value in item.task for value in added)
        yield QualityGateResult(
            name="reference_patch_not_leaked",
            passed=not leaked,
            detail=(
                "task and regression tests do not contain non-trivial added implementation lines"
            ),
        )
        assert candidate.provenance is not None
        yield QualityGateResult(
            name="license_and_provenance_complete",
            passed=bool(candidate.provenance.license_spdx),
            detail=(
                f"SPDX={candidate.provenance.license_spdx}; "
                f"contamination={candidate.provenance.contamination_risk}"
            ),
        )
        yield QualityGateResult(
            name="selection_independent_of_agent_scores",
            passed=True,
            detail="pipeline has no tested-Agent result input or score-based candidate filter",
        )
        alternative_path = (
            self.store.job_dir(candidate.job_id)
            / "candidates"
            / str(candidate.id)
            / "evidence"
            / "patches"
            / "alternative.patch"
        )
        assert candidate.provenance is not None
        distinct = _file_sha256(alternative_path) != candidate.provenance.reference_patch_sha256
        yield QualityGateResult(
            name="alternative_fix_is_distinct",
            passed=distinct,
            detail="alternative patch hash differs from the upstream reference patch",
            evidence_sha256=_file_sha256(alternative_path),
        )

    @staticmethod
    def _selected_test_source(item: CandidateSpec, after_fixture: Path) -> str:
        selector = item.test_command[-1].split(".")
        if len(selector) < 2:
            raise BenchmarkGenerationError("test command must end with ClassName.method_name")
        class_name, method_name = selector[-2:]
        selected = []
        for path in item.regression_test_paths:
            source = (after_fixture / path).read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                raise BenchmarkGenerationError(
                    f"cannot parse regression test {path}: {exc}"
                ) from exc
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                            child.name == method_name
                        ):
                            segment = ast.get_source_segment(source, child)
                            if segment is not None:
                                selected.append(segment)
        if not selected:
            raise BenchmarkGenerationError(
                f"cannot locate selected test {class_name}.{method_name} in frozen test paths"
            )
        return "\n".join(selected)

    def _deduplicate(
        self, candidate: BenchmarkCandidate, seen: Dict[str, UUID]
    ) -> BenchmarkCandidate:
        assert candidate.provenance is not None
        fingerprint = stable_sha256(
            {
                "task": " ".join(candidate.task.lower().split()),
                "patch": candidate.provenance.reference_patch_sha256,
                "lineage": candidate.provenance.fork_lineage,
            }
        )
        if fingerprint in seen:
            return self._reject(
                candidate.model_copy(update={"duplicate_of": seen[fingerprint]}),
                f"duplicate of {seen[fingerprint]}",
            )
        seen[fingerprint] = candidate.id
        gate = QualityGateResult(
            name="exact_multisignal_dedup",
            passed=True,
            detail=f"unique normalized task + patch + fork-lineage fingerprint {fingerprint}",
            evidence_sha256=fingerprint,
        )
        return self._transition(
            candidate,
            BenchmarkCandidateStatus.DEDUPED,
            actor="deduplicator:exact-v1",
            output={"fingerprint": fingerprint},
            updates={"quality_gates": (*candidate.quality_gates, gate)},
        )

    def _reject(self, candidate: BenchmarkCandidate, reason: str) -> BenchmarkCandidate:
        return self._transition(
            candidate,
            BenchmarkCandidateStatus.REJECTED,
            actor="pipeline:quality-gate",
            output={"reason": reason},
            updates={"rejection_reasons": (*candidate.rejection_reasons, reason)},
        )

    def _assert_candidate_integrity(self, candidate: BenchmarkCandidate) -> None:
        base = self.store.job_dir(candidate.job_id) / "candidates" / str(candidate.id)
        expected = candidate.artifact_sha256
        actual = {
            "fixture.before": _tree_sha256(base / "fixtures" / "before"),
            "fixture.after": _tree_sha256(base / "fixtures" / "after"),
            "fixture.mutation": _tree_sha256(base / "fixtures" / "mutation"),
            "fixture.alternative": _tree_sha256(base / "fixtures" / "alternative"),
            "patch.reference": _file_sha256(base / "evidence" / "patches" / "reference.patch"),
            "patch.alternative": _file_sha256(
                base / "evidence" / "patches" / "alternative.patch"
            ),
        }
        if actual != expected:
            raise BenchmarkGenerationError("candidate frozen artifact integrity mismatch")
        for evidence in candidate.command_evidence:
            log_dir = base / "evidence" / evidence.variant
            stdout = log_dir / f"{evidence.repeat_index:02d}.stdout"
            stderr = log_dir / f"{evidence.repeat_index:02d}.stderr"
            if (
                _file_sha256(stdout) != evidence.stdout_sha256
                or _file_sha256(stderr) != evidence.stderr_sha256
            ):
                raise BenchmarkGenerationError("candidate command evidence integrity mismatch")

    def _transition(
        self,
        candidate: BenchmarkCandidate,
        status: BenchmarkCandidateStatus,
        *,
        actor: str,
        output: Mapping[str, object],
        updates: Optional[Mapping[str, object]] = None,
    ) -> BenchmarkCandidate:
        input_hash = stable_sha256(candidate.model_dump(mode="json", round_trip=True))
        transition = CandidateTransition(
            sequence=len(candidate.transitions) + 1,
            from_status=candidate.status,
            to_status=status,
            occurred_at=_utcnow(),
            actor=actor,
            input_sha256=input_hash,
            output_sha256=stable_sha256(output),
        )
        values: Dict[str, object] = {
            "status": status,
            "transitions": (*candidate.transitions, transition),
        }
        values.update(updates or {})
        return candidate.model_copy(update=values)

    @staticmethod
    def _grader_text(command: Sequence[str]) -> str:
        return (
            "#!/usr/bin/env python3\n"
            "import subprocess\n"
            "import sys\n\n"
            f"COMMAND = {list(command)!r}\n"
            "argv = [sys.executable if x in {'python', 'python3', '${PYTHON}'} "
            "else x for x in COMMAND]\n"
            "raise SystemExit(subprocess.run(argv, check=False).returncode)\n"
        )

    @staticmethod
    def _normalize_unified_diff(value: str) -> bytes:
        """Restore the mandatory prefix on blank context lines lost by YAML editors."""
        output = []
        in_hunk = False
        for line in value.splitlines():
            if line.startswith("@@"):
                in_hunk = True
            elif line.startswith(("diff --git ", "--- ", "+++ ")):
                in_hunk = False
            if in_hunk and line == "":
                line = " "
            output.append(line)
        return ("\n".join(output) + "\n").encode("utf-8")

    def _publish_case(self, candidate: BenchmarkCandidate, root: Path) -> PublishedCase:
        assert candidate.fixture_sha256 and candidate.grader_sha256 and candidate.provenance
        base = self.store.job_dir(candidate.job_id) / "candidates" / str(candidate.id)
        fixture_ref = f"evals/fixtures/repos/{candidate.key}"
        grader_ref = f"evals/fixtures/scripts/{candidate.key}.py"
        case_ref = f"evals/cases/{candidate.key}.yaml"
        metadata_ref = f"metadata/{candidate.key}.yaml"
        shutil.copytree(base / "fixtures" / "before", root / fixture_ref)
        observed_argv = candidate.command_evidence[0].argv
        portable_command = ("${PYTHON}", *observed_argv[1:])
        grader = self._grader_text(portable_command)
        grader_path = root / grader_ref
        grader_path.parent.mkdir(parents=True, exist_ok=True)
        grader_path.write_text(grader, encoding="utf-8")
        case = {
            "id": candidate.key,
            "input": {"prompt": candidate.task},
            "context": {"repo_fixture": fixture_ref},
            "expect": {"exit_code": 0},
            "judge": {"type": "script", "script_path": grader_ref, "timeout_seconds": 60},
        }
        case_path = root / case_ref
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(
            yaml.safe_dump(case, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        metadata = {
            "schema_version": "ase-case-meta/v1alpha1",
            "case_id": candidate.key,
            "case_ref": case_ref,
            "split": candidate.target_split,
            "category": "positive",
            "skill_applicable": True,
            "group_keys": {
                "independence_group": candidate.provenance.fork_lineage,
                "repository": candidate.provenance.repository_url,
                "fork_lineage": candidate.provenance.fork_lineage,
                "patch_family": candidate.provenance.after_commit,
            },
            "provenance": {
                "source_type": "git_history",
                "source_revision": candidate.provenance.after_commit,
                "license": candidate.provenance.license_spdx,
                "contamination_risk": candidate.provenance.contamination_risk,
                "synthetic": False,
            },
            "oracle": {"kind": "script", "expected_signal": "regression test exits 0"},
            "tags": ["generated", "real-oss", "offline"],
        }
        metadata_path = root / metadata_ref
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        provenance_path = root / "provenance" / f"{candidate.key}.json"
        provenance_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_payload = (
            json.dumps(candidate.provenance.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        )
        provenance_path.write_text(provenance_payload, encoding="utf-8")
        return PublishedCase(
            candidate_id=candidate.id,
            case_id=candidate.key,
            case_sha256=_file_sha256(case_path),
            fixture_sha256=_tree_sha256(root / fixture_ref),
            grader_sha256=_file_sha256(grader_path),
            provenance_sha256=_file_sha256(provenance_path),
        )

    @staticmethod
    def _write_dataset_manifest(
        root: Path,
        version: BenchmarkDatasetVersion,
        candidates: Sequence[BenchmarkCandidate],
    ) -> None:
        manifest = {
            "schema_version": "ase-dataset/v1alpha1",
            "name": version.name,
            "version": version.version,
            "description": (
                "Audited offline benchmark candidates reconstructed from real OSS history."
            ),
            "domain": "python-software-engineering",
            "license": "mixed-upstream-see-provenance",
            "runner_name": "skill-up",
            "runner_version": "0.5.0",
            "demo_only": False,
            "expected_case_count": len(candidates),
            "case_metadata": [f"metadata/{item.key}.yaml" for item in candidates],
            "minimum_category_counts": {"positive": len(candidates)},
        }
        (root / "dataset.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
