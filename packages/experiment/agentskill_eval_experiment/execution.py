"""Service-free P0 execution of persisted paired experiment plans."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple
from uuid import UUID, uuid4

from agentskill_eval_contracts import (
    ArtifactEntry,
    ArtifactManifest,
    AttemptStatus,
    EvaluationOutcome,
    ExecutionStatus,
    Run,
    RunAttempt,
    RunMeasurement,
    SecurityScanEvidence,
    SkillActivationEvidence,
)
from agentskill_eval_experiment.planning import (
    CaseExecutionSpec,
    PlannedExperiment,
    VariantRuntimeSpec,
)
from agentskill_eval_experiment.security import (
    ExactSecretScanner,
    SecretLeakError,
    SecretScanResult,
)
from agentskill_eval_experiment.storage import ExperimentLayout, LocalExperimentStore
from agentskill_eval_runner_adapters import (
    ExitReason,
    RunnerAdapter,
    RunnerRequest,
    RunnerResult,
    RunnerSkillEvidence,
    RunnerStatus,
)


@dataclass(frozen=True)
class ExecutionRecord:
    run_id: UUID
    variant_id: UUID
    runner_status: RunnerStatus
    execution_status: ExecutionStatus
    evaluation_outcome: Optional[EvaluationOutcome]
    attempt_no: int


@dataclass(frozen=True)
class LocalExecutionSummary:
    experiment_id: UUID
    records: Tuple[ExecutionRecord, ...]

    @property
    def completed_runs(self) -> int:
        return sum(record.execution_status == ExecutionStatus.COMPLETED for record in self.records)

    @property
    def invalid_runs(self) -> int:
        return sum(
            record.evaluation_outcome == EvaluationOutcome.INVALID for record in self.records
        )


@dataclass(frozen=True)
class ArchivedResult:
    artifacts: ArtifactManifest
    security_scan: SecurityScanEvidence


class LocalExperimentExecutor:
    def __init__(
        self,
        store: LocalExperimentStore,
        adapter: RunnerAdapter,
        *,
        worker_id: str = "local-worker",
        progress_sink: Optional[Callable[[ExecutionRecord, int, int], None]] = None,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.worker_id = worker_id
        self.progress_sink = progress_sink
        self.secret_scanner = ExactSecretScanner()

    async def execute(self, plan: PlannedExperiment) -> LocalExecutionSummary:
        records: List[ExecutionRecord] = []
        total_runs = sum(len(block.runs) for block in plan.blocks)
        for planned_block in plan.blocks:
            runs = {run.variant_id: run for run in planned_block.runs}
            for variant_id in planned_block.block.execution_order:
                record = await self._execute_run(
                    plan.experiment.id,
                    runs[variant_id].id,
                    planned_block.case,
                    plan.runtime_for(variant_id),
                )
                records.append(record)
                if self.progress_sink is not None:
                    self.progress_sink(record, len(records), total_runs)
        return LocalExecutionSummary(plan.experiment.id, tuple(records))

    async def _execute_run(
        self,
        experiment_id: UUID,
        run_id: UUID,
        case: CaseExecutionSpec,
        runtime: VariantRuntimeSpec,
    ) -> ExecutionRecord:
        with self.store.run_lock(experiment_id, run_id):
            run = self.store.load_run(experiment_id, run_id)
            if run.execution_status in {
                ExecutionStatus.COMPLETED,
                ExecutionStatus.INFRA_FAILED,
                ExecutionStatus.CANCELLED,
            }:
                return ExecutionRecord(
                    run.id,
                    run.variant_id,
                    self._status_for_terminal_run(run),
                    run.execution_status,
                    run.evaluation_outcome,
                    run.lease_generation,
                )
            if run.execution_status != ExecutionStatus.QUEUED:
                raise ValueError(
                    f"run {run.id} is not executable from {run.execution_status.value}"
                )

            attempt_no = run.lease_generation + 1
            now = datetime.now(timezone.utc)
            attempt = RunAttempt(
                id=uuid4(),
                run_id=run.id,
                attempt_no=attempt_no,
                lease_generation=attempt_no,
                fencing_token=uuid4(),
                status=AttemptStatus.CLAIMED,
                worker_id=self.worker_id,
                claimed_at=now,
            )
            self.store.save_attempt(experiment_id, attempt)
            run = self._advance_run(
                run,
                ExecutionStatus.LEASED,
                lease_generation=attempt_no,
                active_attempt_id=attempt.id,
            )
            attempt = self._advance_attempt(experiment_id, attempt, AttemptStatus.PREPARING)
            run = self._advance_run(run, ExecutionStatus.PREPARING)

            request = self._request(run, attempt, case, runtime)
            try:
                validation = await self.adapter.validate(request)
            except Exception as exc:  # adapter boundary
                activation = self._activation_evidence(run, attempt, None, runtime)
                return self._finalize_infra_failure(
                    run,
                    attempt,
                    RunnerStatus.ERROR,
                    "runner_validation_exception",
                    f"{type(exc).__name__}: {exc}",
                    activation_evidence=activation,
                    secret_env=runtime.secret_env,
                )
            activation = self._activation_evidence(run, attempt, validation.skill_evidence, runtime)
            if not validation.valid:
                detail = "\n".join(validation.errors) or validation.stderr
                return self._finalize_infra_failure(
                    run,
                    attempt,
                    RunnerStatus.ERROR,
                    "runner_validation_failed",
                    detail,
                    activation_evidence=activation,
                    secret_env=runtime.secret_env,
                )

            attempt = self._advance_attempt(experiment_id, attempt, AttemptStatus.RUNNING)
            run = self._advance_run(run, ExecutionStatus.RUNNING)
            try:
                result = await self.adapter.execute(request)
            except Exception as exc:  # adapter boundary: convert unexpected failures to evidence
                result = RunnerResult(
                    execution_id=request.execution_id,
                    case_id=request.case_id,
                    status=RunnerStatus.ERROR,
                    exit_reason=ExitReason.EXECUTION_ERROR,
                    process_exit_code=None,
                    stderr=f"{type(exc).__name__}: {exc}",
                )

            run = self._advance_run(run, ExecutionStatus.GRADING)
            try:
                archived = self._archive_runner_output(run, attempt, request, result)
            except SecretLeakError as exc:
                self._purge_contaminated_runtime(request.run_dir)
                scan = self._security_evidence(run, attempt, exc.result, status="blocked")
                return self._finalize_infra_failure(
                    run,
                    attempt,
                    RunnerStatus.ERROR,
                    "secret_leak_detected",
                    str(exc),
                    activation_evidence=activation,
                    security_scan=scan,
                    secret_env=runtime.secret_env,
                )
            except (OSError, ValueError) as exc:
                return self._finalize_infra_failure(
                    run,
                    attempt,
                    RunnerStatus.ERROR,
                    "artifact_archival_failed",
                    f"{type(exc).__name__}: {exc}",
                    activation_evidence=activation,
                    secret_env=runtime.secret_env,
                )
            run = self._advance_run(run, ExecutionStatus.PERSISTING)
            return self._finalize_result(
                run,
                attempt,
                result,
                archived.artifacts,
                activation,
                archived.security_scan,
            )

    def _request(
        self,
        run: Run,
        attempt: RunAttempt,
        case: CaseExecutionSpec,
        runtime: VariantRuntimeSpec,
    ) -> RunnerRequest:
        run_dir = (
            self.store.workspace
            / ".runtime"
            / str(run.experiment_id)
            / str(run.id)
            / str(attempt.attempt_no)
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        layout = ExperimentLayout(self.store.workspace, run.experiment_id)
        frozen_source = layout.frozen_input_files("case_source", case.id)
        source_relative = case.case_file.resolve(strict=True).relative_to(
            case.source_eval_dir.resolve(strict=True)
        )
        frozen_case_file = frozen_source / source_relative
        frozen_skill = layout.frozen_input_files("skill", run.variant_id)
        return RunnerRequest(
            execution_id=str(attempt.id),
            case_id=case.runner_case_id,
            variant=str(run.variant_id),
            source_eval_dir=frozen_source,
            case_file=frozen_case_file,
            run_dir=run_dir,
            engine=runtime.engine,
            environment=runtime.environment,
            timeout_seconds=runtime.timeout_seconds,
            max_turns=runtime.max_turns,
            skill_path=frozen_skill if frozen_skill.is_dir() else runtime.skill_path,
            mcp=runtime.mcp,
            collect_artifacts=runtime.collect_artifacts,
            secret_env=runtime.secret_env,
        )

    def _archive_runner_output(
        self,
        run: Run,
        attempt: RunAttempt,
        request: RunnerRequest,
        result: RunnerResult,
    ) -> ArchivedResult:
        layout = ExperimentLayout(self.store.workspace, run.experiment_id)
        destination_root = layout.raw_runner(run.id, attempt.attempt_no)
        payloads: List[Tuple[str, bytes]] = []
        source_root = request.run_dir / "runner-output" / "iteration-1"
        for observed in result.artifacts:
            relative = PurePosixPath(observed.path)
            if relative.is_absolute() or ".." in relative.parts or str(relative) != observed.path:
                raise ValueError(f"runner artifact path is unsafe: {observed.path}")
            source = source_root / observed.path
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"runner artifact is not a regular file: {observed.path}")
            content = source.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != observed.sha256 or len(content) != observed.size_bytes:
                raise ValueError(f"runner artifact changed after observation: {observed.path}")
            payloads.append((observed.path, content))

        for name, content in (
            ("platform-stdout.log", result.stdout.encode("utf-8")),
            ("platform-stderr.log", result.stderr.encode("utf-8")),
        ):
            if not content:
                continue
            payloads.append((name, content))
        scan_result = self.secret_scanner.scan(payloads, request.secret_env)
        if not scan_result.clean:
            raise SecretLeakError(scan_result)
        entries: List[ArtifactEntry] = []
        for name, content in payloads:
            self.store.blobs.put_bytes(content)
            self.store.writer.write(destination_root / name, content)
            entries.append(self._artifact_entry(name, content))
        return ArchivedResult(
            artifacts=ArtifactManifest(artifacts=tuple(entries)),
            security_scan=self._security_evidence(run, attempt, scan_result, status="clean"),
        )

    @staticmethod
    def _artifact_entry(relative_path: str, content: bytes) -> ArtifactEntry:
        media_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        return ArtifactEntry(
            path=f"raw-runner/{relative_path}",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            media_type=media_type,
        )

    @staticmethod
    def _purge_contaminated_runtime(run_dir: Path) -> None:
        """Remove Runner-owned material after a Secret match before publishing evidence."""
        shutil.rmtree(run_dir)

    def _finalize_result(
        self,
        run: Run,
        attempt: RunAttempt,
        result: RunnerResult,
        artifacts: ArtifactManifest,
        activation_evidence: SkillActivationEvidence,
        security_scan: SecurityScanEvidence,
    ) -> ExecutionRecord:
        if result.status in {RunnerStatus.PASS, RunnerStatus.FAIL}:
            attempt = self._terminal_attempt(attempt, AttemptStatus.COMPLETED)
            outcome = (
                EvaluationOutcome.PASS
                if result.status == RunnerStatus.PASS
                else EvaluationOutcome.FAIL
            )
            default_score = 1.0 if outcome == EvaluationOutcome.PASS else 0.0
            score = self._grading_score(result.grading, default_score)
            run = self._terminal_run(
                run,
                ExecutionStatus.COMPLETED,
                evaluation_outcome=outcome,
                final_score=score,
            )
        elif result.exit_reason == ExitReason.CANCELLED:
            attempt = self._terminal_attempt(attempt, AttemptStatus.CANCELLED)
            run = self._advance_run(run, ExecutionStatus.CANCEL_REQUESTED)
            run = self._terminal_run(run, ExecutionStatus.CANCELLED)
        else:
            error_code = result.exit_reason.value
            attempt = self._terminal_attempt(
                attempt,
                AttemptStatus.FAILED,
                error_code=error_code,
                error_detail={"stderr": result.stderr[-4000:]},
            )
            run = self._terminal_run(
                run,
                ExecutionStatus.INFRA_FAILED,
                evaluation_outcome=EvaluationOutcome.INVALID,
            )
        measurement = self._measurement(run, attempt, result)
        selected = self.store.commit_attempt(
            run,
            attempt,
            artifacts,
            measurement,
            activation_evidence,
            security_scan,
        )
        return ExecutionRecord(
            selected.id,
            selected.variant_id,
            result.status,
            selected.execution_status,
            selected.evaluation_outcome,
            attempt.attempt_no,
        )

    def _finalize_infra_failure(
        self,
        run: Run,
        attempt: RunAttempt,
        runner_status: RunnerStatus,
        error_code: str,
        detail: str,
        *,
        activation_evidence: Optional[SkillActivationEvidence] = None,
        security_scan: Optional[SecurityScanEvidence] = None,
        secret_env: Optional[Mapping[str, str]] = None,
    ) -> ExecutionRecord:
        secret_env = secret_env or {}
        detail = self.secret_scanner.redact(detail, secret_env)
        attempt = self._terminal_attempt(
            attempt,
            AttemptStatus.FAILED,
            error_code=error_code,
            error_detail={"detail": detail[-4000:]},
        )
        run = self._terminal_run(
            run,
            ExecutionStatus.INFRA_FAILED,
            evaluation_outcome=EvaluationOutcome.INVALID,
        )
        measurement = RunMeasurement(
            run_id=run.id,
            attempt_id=attempt.id,
            runner_status=runner_status.value,
            runner_exit_reason=error_code,
        )
        if activation_evidence is None:
            activation_evidence = self._activation_evidence(run, attempt, None, None)
        if security_scan is None:
            security_scan = SecurityScanEvidence(
                run_id=run.id,
                attempt_id=attempt.id,
                status="not_run",
                configured_secret_count=len(secret_env),
                scanned_files=0,
                scanned_bytes=0,
                note="Runner output was unavailable for pre-persistence scanning.",
            )
        selected = self.store.commit_attempt(
            run,
            attempt,
            ArtifactManifest(),
            measurement,
            activation_evidence,
            security_scan,
        )
        return ExecutionRecord(
            selected.id,
            selected.variant_id,
            runner_status,
            selected.execution_status,
            selected.evaluation_outcome,
            attempt.attempt_no,
        )

    @staticmethod
    def _activation_evidence(
        run: Run,
        attempt: RunAttempt,
        observed: Optional[RunnerSkillEvidence],
        runtime: Optional[VariantRuntimeSpec],
    ) -> SkillActivationEvidence:
        skill_expected = runtime is not None and runtime.skill_path is not None
        if observed is None:
            return SkillActivationEvidence(
                run_id=run.id,
                attempt_id=attempt.id,
                skill_expected=skill_expected,
                installation_method="validation_unavailable",
                unavailable_reasons={
                    "installed": "Runner validation did not return installation evidence",
                    "baseline_clean": "Runner validation did not return workspace evidence",
                    "discovered": "unsupported",
                    "read": "unsupported",
                    "activated": "unsupported",
                    "followed": "unsupported",
                },
            )
        return SkillActivationEvidence(
            run_id=run.id,
            attempt_id=attempt.id,
            skill_expected=observed.skill_expected,
            installed=observed.installed,
            baseline_clean=observed.baseline_clean,
            installation_method=observed.installation_method,
            compiled_eval_sha256=observed.compiled_eval_sha256,
            installed_skill_sha256=observed.installed_skill_sha256,
            discovered=observed.discovered,
            read=observed.read,
            activated=observed.activated,
            followed=observed.followed,
            unavailable_reasons=dict(observed.unavailable_reasons),
        )

    @staticmethod
    def _security_evidence(
        run: Run,
        attempt: RunAttempt,
        result: SecretScanResult,
        *,
        status: Literal["clean", "blocked"],
    ) -> SecurityScanEvidence:
        return SecurityScanEvidence(
            run_id=run.id,
            attempt_id=attempt.id,
            status=status,
            configured_secret_count=result.configured_secret_count,
            scanned_files=result.scanned_files,
            scanned_bytes=result.scanned_bytes,
            matched_secret_names=result.matched_secret_names,
        )

    def _advance_run(self, run: Run, status: ExecutionStatus, **updates: Any) -> Run:
        payload: Dict[str, Any] = run.model_dump(mode="python", round_trip=True)
        payload.update(updates)
        payload["execution_status"] = status
        updated = Run.model_validate(payload)
        self.store.save_run(updated)
        return updated

    def _terminal_run(self, run: Run, status: ExecutionStatus, **updates: Any) -> Run:
        updates["finished_at"] = datetime.now(timezone.utc)
        return self._advance_run(run, status, **updates)

    def _advance_attempt(
        self,
        experiment_id: UUID,
        attempt: RunAttempt,
        status: AttemptStatus,
    ) -> RunAttempt:
        payload: Dict[str, Any] = attempt.model_dump(mode="python", round_trip=True)
        payload["status"] = status
        updated = RunAttempt.model_validate(payload)
        self.store.save_attempt(experiment_id, updated)
        return updated

    @staticmethod
    def _terminal_attempt(
        attempt: RunAttempt,
        status: AttemptStatus,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[Mapping[str, Any]] = None,
    ) -> RunAttempt:
        payload: Dict[str, Any] = attempt.model_dump(mode="python", round_trip=True)
        payload.update(
            {
                "status": status,
                "finished_at": datetime.now(timezone.utc),
                "error_code": error_code,
                "error_detail": dict(error_detail or {}),
            }
        )
        return RunAttempt.model_validate(payload)

    @staticmethod
    def _grading_score(grading: Mapping[str, Any], default: float) -> float:
        score = grading.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1:
            return float(score)
        return default

    @staticmethod
    def _measurement(run: Run, attempt: RunAttempt, result: RunnerResult) -> RunMeasurement:
        return RunMeasurement(
            run_id=run.id,
            attempt_id=attempt.id,
            runner_status=result.status.value,
            runner_exit_reason=result.exit_reason.value,
            process_exit_code=result.process_exit_code,
            duration_ms=result.duration_ms,
            turns=result.turns,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    @staticmethod
    def _status_for_terminal_run(run: Run) -> RunnerStatus:
        if run.evaluation_outcome == EvaluationOutcome.PASS:
            return RunnerStatus.PASS
        if run.evaluation_outcome == EvaluationOutcome.FAIL:
            return RunnerStatus.FAIL
        return RunnerStatus.ERROR
