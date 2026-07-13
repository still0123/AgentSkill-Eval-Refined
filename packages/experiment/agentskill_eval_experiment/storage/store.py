"""P0 manifest store, layout, recovery, and index rebuilding."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Type, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from agentskill_eval_contracts import (
    ArtifactManifest,
    AttemptStatus,
    ExecutionStatus,
    ExperimentManifest,
    ExperimentVariant,
    PairBlock,
    Run,
    RunAttempt,
    RunMeasurement,
    validate_attempt_transition,
    validate_run_transition,
)
from agentskill_eval_experiment.storage.atomic import (
    AtomicFileWriter,
    StagedWrite,
    fsync_directory,
)
from agentskill_eval_experiment.storage.blobs import ContentAddressedBlobStore
from agentskill_eval_experiment.storage.errors import (
    ImmutableManifestError,
    IntegrityError,
)
from agentskill_eval_experiment.storage.index import LocalSqliteIndex, ManifestIndexRecord
from agentskill_eval_experiment.storage.locks import LocalRunLock
from agentskill_eval_experiment.storage.manifests import (
    ManifestEnvelope,
    envelope_for_model,
    load_model,
    model_bytes,
    parse_envelope,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
TERMINAL_RUN_STATUSES = {
    ExecutionStatus.COMPLETED,
    ExecutionStatus.INFRA_FAILED,
    ExecutionStatus.CANCELLED,
}
TERMINAL_ATTEMPT_STATUSES = {
    AttemptStatus.COMPLETED,
    AttemptStatus.FAILED,
    AttemptStatus.FENCED,
    AttemptStatus.CANCELLED,
}
KNOWN_MANIFEST_MODELS: Dict[str, Type[BaseModel]] = {
    model_type.__name__: model_type
    for model_type in (
        ArtifactManifest,
        ExperimentManifest,
        ExperimentVariant,
        PairBlock,
        Run,
        RunAttempt,
        RunMeasurement,
    )
}


@dataclass(frozen=True)
class RecoveryReport:
    promoted_temporary_files: Tuple[str, ...]
    removed_duplicate_temporary_files: Tuple[str, ...]
    quarantined_files: Tuple[str, ...]
    unfinished_run_ids: Tuple[UUID, ...]


class ExperimentLayout:
    """Construct all persisted paths from validated UUID identifiers."""

    def __init__(self, workspace: Path, experiment_id: UUID) -> None:
        self.workspace = workspace
        self.experiment_id = experiment_id
        self.root = workspace / "experiments" / str(experiment_id)

    @property
    def experiment(self) -> Path:
        return self.root / "experiment.json"

    @property
    def index(self) -> Path:
        return self.root / "index.sqlite"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def variant(self, variant_id: UUID) -> Path:
        return self.root / "variants" / f"{variant_id}.json"

    def pair_block(self, block_id: UUID) -> Path:
        return self.root / "pair-blocks" / f"{block_id}.json"

    def run_root(self, run_id: UUID) -> Path:
        return self.root / "runs" / str(run_id)

    def run(self, run_id: UUID) -> Path:
        return self.run_root(run_id) / "run.json"

    def run_lock(self, run_id: UUID) -> Path:
        return self.run_root(run_id) / "run.lock"

    def attempt_root(self, run_id: UUID, attempt_no: int) -> Path:
        if attempt_no < 1:
            raise ValueError("attempt_no must be at least 1")
        return self.run_root(run_id) / "attempts" / str(attempt_no)

    def attempt(self, run_id: UUID, attempt_no: int) -> Path:
        return self.attempt_root(run_id, attempt_no) / "attempt.json"

    def artifact_manifest(self, run_id: UUID, attempt_no: int) -> Path:
        return self.attempt_root(run_id, attempt_no) / "artifacts" / "manifest.json"

    def measurement(self, run_id: UUID, attempt_no: int) -> Path:
        return self.attempt_root(run_id, attempt_no) / "measurement.json"

    def raw_runner(self, run_id: UUID, attempt_no: int) -> Path:
        path = self.attempt_root(run_id, attempt_no) / "raw-runner"
        path.mkdir(parents=True, exist_ok=True)
        return path


class LocalExperimentStore:
    """Manifest truth source for the service-free P0 runtime."""

    def __init__(self, workspace: Path, writer: Optional[AtomicFileWriter] = None) -> None:
        self.workspace = workspace
        self.writer = writer or AtomicFileWriter()
        self.blobs = ContentAddressedBlobStore(workspace / "objects", self.writer)
        self.quarantine = workspace / "quarantine"
        (workspace / "experiments").mkdir(parents=True, exist_ok=True)
        self.quarantine.mkdir(parents=True, exist_ok=True)

    def save_experiment(self, manifest: ExperimentManifest) -> None:
        layout = ExperimentLayout(self.workspace, manifest.id)
        self._write_model(layout, layout.experiment, manifest, immutable=True)

    def load_experiment(self, experiment_id: UUID) -> ExperimentManifest:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return self._load_model(layout.experiment, ExperimentManifest)

    def save_variant(self, variant: ExperimentVariant) -> None:
        layout = ExperimentLayout(self.workspace, variant.experiment_id)
        self._write_model(layout, layout.variant(variant.id), variant, immutable=True)

    def load_variant(self, experiment_id: UUID, variant_id: UUID) -> ExperimentVariant:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return self._load_model(layout.variant(variant_id), ExperimentVariant)

    def list_variants(self, experiment_id: UUID) -> Tuple[ExperimentVariant, ...]:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return tuple(
            self._load_model(path, ExperimentVariant)
            for path in sorted((layout.root / "variants").glob("*.json"))
        )

    def save_pair_block(self, block: PairBlock) -> None:
        layout = ExperimentLayout(self.workspace, block.experiment_id)
        self._write_model(layout, layout.pair_block(block.id), block, immutable=True)

    def load_pair_block(self, experiment_id: UUID, block_id: UUID) -> PairBlock:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return self._load_model(layout.pair_block(block_id), PairBlock)

    def list_pair_blocks(self, experiment_id: UUID) -> Tuple[PairBlock, ...]:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return tuple(
            self._load_model(path, PairBlock)
            for path in sorted((layout.root / "pair-blocks").glob("*.json"))
        )

    def save_run(self, run: Run) -> None:
        layout = ExperimentLayout(self.workspace, run.experiment_id)
        path = layout.run(run.id)
        if path.exists():
            current = self._load_model(path, Run)
            if current.execution_status != run.execution_status:
                validate_run_transition(current.execution_status, run.execution_status)
            if run.lease_generation < current.lease_generation:
                raise ValueError("lease_generation cannot move backwards")
        elif run.execution_status != ExecutionStatus.CREATED:
            raise ValueError("new runs must be persisted in CREATED state")
        self._write_model(layout, path, run, immutable=False)

    def load_run(self, experiment_id: UUID, run_id: UUID) -> Run:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return self._load_model(layout.run(run_id), Run)

    def list_runs(self, experiment_id: UUID) -> Tuple[Run, ...]:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return tuple(
            self._load_model(path, Run)
            for path in sorted(layout.root.glob("runs/*/run.json"))
        )

    def load_selected_measurement(
        self, experiment_id: UUID, run: Run
    ) -> Optional[RunMeasurement]:
        attempt = self.load_selected_attempt(experiment_id, run)
        if attempt is None:
            return None
        layout = ExperimentLayout(self.workspace, experiment_id)
        measurement_path = layout.measurement(run.id, attempt.attempt_no)
        if not measurement_path.is_file():
            return None
        return self._load_model(measurement_path, RunMeasurement)

    def load_selected_attempt(
        self, experiment_id: UUID, run: Run
    ) -> Optional[RunAttempt]:
        if run.active_attempt_id is None:
            return None
        layout = ExperimentLayout(self.workspace, experiment_id)
        for attempt_path in sorted(layout.run_root(run.id).glob("attempts/*/attempt.json")):
            attempt = self._load_model(attempt_path, RunAttempt)
            if attempt.id == run.active_attempt_id:
                return attempt
        return None

    def save_attempt(self, experiment_id: UUID, attempt: RunAttempt) -> None:
        """Persist a physical Attempt and enforce monotonic state transitions."""
        layout = ExperimentLayout(self.workspace, experiment_id)
        path = layout.attempt(attempt.run_id, attempt.attempt_no)
        if path.exists():
            current = self._load_model(path, RunAttempt)
            if current.id != attempt.id or current.run_id != attempt.run_id:
                raise ValueError("attempt identity cannot change")
            if current.attempt_no != attempt.attempt_no:
                raise ValueError("attempt number cannot change")
            if current.lease_generation != attempt.lease_generation:
                raise ValueError("attempt lease_generation cannot change")
            if current.fencing_token != attempt.fencing_token:
                raise ValueError("attempt fencing_token cannot change")
            if current.claimed_at != attempt.claimed_at or current.worker_id != attempt.worker_id:
                raise ValueError("attempt claimant cannot change")
            if current.status in TERMINAL_ATTEMPT_STATUSES and current != attempt:
                raise ValueError("terminal attempt cannot change")
            if current.status != attempt.status:
                validate_attempt_transition(current.status, attempt.status)
        self._write_model(layout, path, attempt, immutable=False)

    def load_attempt(
        self, experiment_id: UUID, run_id: UUID, attempt_no: int
    ) -> RunAttempt:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return self._load_model(layout.attempt(run_id, attempt_no), RunAttempt)

    def save_artifact_manifest(
        self,
        experiment_id: UUID,
        run_id: UUID,
        attempt_no: int,
        attempt_id: UUID,
        artifacts: ArtifactManifest,
    ) -> None:
        """Publish an immutable artifact manifest after all referenced bytes exist."""
        layout = ExperimentLayout(self.workspace, experiment_id)
        self._write_model(
            layout,
            layout.artifact_manifest(run_id, attempt_no),
            artifacts,
            immutable=True,
            entity_id=str(attempt_id),
        )

    def save_measurement(
        self,
        experiment_id: UUID,
        run_id: UUID,
        attempt_no: int,
        measurement: RunMeasurement,
    ) -> None:
        if measurement.run_id != run_id:
            raise ValueError("measurement.run_id must match run_id")
        layout = ExperimentLayout(self.workspace, experiment_id)
        self._write_model(
            layout,
            layout.measurement(run_id, attempt_no),
            measurement,
            immutable=True,
            entity_id=str(measurement.attempt_id),
        )

    def load_measurement(
        self, experiment_id: UUID, run_id: UUID, attempt_no: int
    ) -> RunMeasurement:
        layout = ExperimentLayout(self.workspace, experiment_id)
        return self._load_model(layout.measurement(run_id, attempt_no), RunMeasurement)

    def commit_attempt(
        self,
        run: Run,
        attempt: RunAttempt,
        artifacts: Optional[ArtifactManifest] = None,
        measurement: Optional[RunMeasurement] = None,
    ) -> Run:
        """Persist a terminal Attempt before atomically advancing its Run pointer."""
        if attempt.run_id != run.id:
            raise ValueError("attempt.run_id must match run.id")
        if attempt.status not in TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("only terminal attempts can be committed")
        if run.execution_status not in TERMINAL_RUN_STATUSES:
            raise ValueError("committed attempts require a terminal Run result")
        if attempt.lease_generation != run.lease_generation:
            raise ValueError("attempt lease_generation must match the Run generation")
        if measurement is not None and measurement.attempt_id != attempt.id:
            raise ValueError("measurement.attempt_id must match attempt.id")

        attempt_envelope = envelope_for_model(attempt)
        self.save_attempt(run.experiment_id, attempt)
        if measurement is not None:
            self.save_measurement(
                run.experiment_id,
                run.id,
                attempt.attempt_no,
                measurement,
            )
        if artifacts is not None:
            self.save_artifact_manifest(
                run.experiment_id,
                run.id,
                attempt.attempt_no,
                attempt.id,
                artifacts,
            )

        run_payload = run.model_dump(mode="python", round_trip=True)
        run_payload["active_attempt_id"] = attempt.id
        run_payload["selected_attempt_sha256"] = attempt_envelope.payload_sha256
        selected_run = Run.model_validate(run_payload)
        self.save_run(selected_run)
        return selected_run

    def run_lock(self, experiment_id: UUID, run_id: UUID) -> LocalRunLock:
        return LocalRunLock(ExperimentLayout(self.workspace, experiment_id).run_lock(run_id))

    def rebuild_index(self, experiment_id: UUID) -> List[ManifestIndexRecord]:
        layout = ExperimentLayout(self.workspace, experiment_id)
        records = [
            self._index_record(layout, path, self._validate_manifest_bytes(path.read_bytes()))
            for path in self._manifest_paths(layout)
        ]
        LocalSqliteIndex(layout.index).replace_all(records)
        return records

    def recover(self) -> RecoveryReport:
        promoted: List[str] = []
        removed: List[str] = []
        quarantined: List[str] = []

        temporary_files = sorted(
            path for path in self.workspace.rglob(".tmp-*") if self.quarantine not in path.parents
        )
        for temporary in temporary_files:
            target = self.writer.target_for_temporary(temporary)
            if target is None:
                quarantined.append(self._quarantine(temporary))
                continue
            try:
                temporary_envelope = self._validate_manifest_bytes(temporary.read_bytes())
            except (OSError, IntegrityError):
                quarantined.append(self._quarantine(temporary))
                continue

            if target.exists():
                try:
                    target_envelope = self._validate_manifest_bytes(target.read_bytes())
                except (OSError, IntegrityError):
                    quarantined.append(self._quarantine(target))
                    self.writer.commit(StagedWrite(target=target, temporary=temporary))
                    promoted.append(str(target.relative_to(self.workspace)))
                    continue
                if target_envelope.payload_sha256 == temporary_envelope.payload_sha256:
                    temporary.unlink()
                    fsync_directory(temporary.parent)
                    removed.append(str(temporary.relative_to(self.workspace)))
                else:
                    quarantined.append(self._quarantine(temporary))
            else:
                self.writer.commit(StagedWrite(target=target, temporary=temporary))
                promoted.append(str(target.relative_to(self.workspace)))

        unfinished: List[UUID] = []
        for experiment_directory in sorted((self.workspace / "experiments").iterdir()):
            if not experiment_directory.is_dir():
                continue
            try:
                experiment_id = UUID(experiment_directory.name)
            except ValueError:
                continue
            layout = ExperimentLayout(self.workspace, experiment_id)
            for path in list(self._manifest_paths(layout)):
                try:
                    self._validate_manifest_bytes(path.read_bytes())
                except (OSError, IntegrityError):
                    quarantined.append(self._quarantine(path))
            for run_path in sorted(layout.root.glob("runs/*/run.json")):
                try:
                    run = self._load_model(run_path, Run)
                except (OSError, IntegrityError):
                    continue
                if run.execution_status not in TERMINAL_RUN_STATUSES:
                    unfinished.append(run.id)
            self.rebuild_index(experiment_id)

        return RecoveryReport(
            promoted_temporary_files=tuple(promoted),
            removed_duplicate_temporary_files=tuple(removed),
            quarantined_files=tuple(quarantined),
            unfinished_run_ids=tuple(unfinished),
        )

    def _write_model(
        self,
        layout: ExperimentLayout,
        path: Path,
        model: BaseModel,
        *,
        immutable: bool,
        entity_id: Optional[str] = None,
    ) -> None:
        content = model_bytes(model)
        new_envelope = parse_envelope(content)
        if path.exists() and immutable:
            existing_content = path.read_bytes()
            self._validate_manifest_bytes(existing_content)
            if existing_content != content:
                raise ImmutableManifestError(f"immutable manifest already exists: {path}")
            return
        self.writer.write(path, content)
        LocalSqliteIndex(layout.index).upsert(
            self._index_record(layout, path, new_envelope, entity_id=entity_id)
        )

    @staticmethod
    def _load_model(path: Path, model_type: Type[ModelT]) -> ModelT:
        try:
            return load_model(path.read_bytes(), model_type)
        except OSError as error:
            raise IntegrityError(f"cannot read manifest {path}: {error}") from error

    @staticmethod
    def _validate_manifest_bytes(content: bytes) -> ManifestEnvelope:
        envelope = parse_envelope(content)
        model_type = KNOWN_MANIFEST_MODELS.get(envelope.model_name)
        if model_type is not None:
            load_model(content, model_type)
        return envelope

    @staticmethod
    def _manifest_paths(layout: ExperimentLayout) -> Iterable[Path]:
        patterns = (
            "experiment.json",
            "variants/*.json",
            "pair-blocks/*.json",
            "runs/*/run.json",
            "runs/*/attempts/*/attempt.json",
            "runs/*/attempts/*/measurement.json",
            "runs/*/attempts/*/artifacts/manifest.json",
            "runs/*/grading/*.json",
        )
        for pattern in patterns:
            yield from sorted(layout.root.glob(pattern))

    @staticmethod
    def _index_record(
        layout: ExperimentLayout,
        path: Path,
        envelope: ManifestEnvelope,
        *,
        entity_id: Optional[str] = None,
    ) -> ManifestIndexRecord:
        payload = envelope.payload
        resolved_entity_id = entity_id or str(
            payload.get("id") or payload.get("run_id") or path.relative_to(layout.root)
        )
        status_value = payload.get("execution_status") or payload.get("status")
        outcome_value = payload.get("evaluation_outcome")
        return ManifestIndexRecord(
            relative_path=str(path.relative_to(layout.root)),
            kind=envelope.model_name,
            entity_id=resolved_entity_id,
            experiment_id=str(layout.experiment_id),
            payload_sha256=envelope.payload_sha256,
            semantic_sha256=envelope.semantic_sha256,
            status=str(status_value) if status_value is not None else None,
            evaluation_outcome=str(outcome_value) if outcome_value is not None else None,
        )

    def _quarantine(self, path: Path) -> str:
        self.quarantine.mkdir(parents=True, exist_ok=True)
        destination = self.quarantine / f"{uuid4()}-{path.name}"
        os.replace(path, destination)
        fsync_directory(destination.parent)
        if path.parent.exists():
            fsync_directory(path.parent)
        return str(destination.relative_to(self.workspace))
