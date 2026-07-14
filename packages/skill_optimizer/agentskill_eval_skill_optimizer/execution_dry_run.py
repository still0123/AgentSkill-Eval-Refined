"""Stage 3B binding and Process-only rehearsal for a real evolution plan."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional, Tuple, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator

from agentskill_eval_benchmark_gen import (
    BenchmarkStore,
    DatasetLoader,
    OptimizationBenchmarkPublisher,
    OptimizationBenchmarkRelease,
    SplitDatasetReference,
)
from agentskill_eval_contracts import FrozenModel, canonical_json, stable_sha256
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_skill_optimizer.execution_plan import (
    DatasetPlanDescriptor,
    EvolutionExecutionPlan,
    RealEvolutionExecutionPlanner,
)

AdaptiveStage = Literal["validation_search", "regression_dev"]
WithheldStage = Literal["validation_confirm", "locked_test"]


class EvolutionDryRunError(RuntimeError):
    """Raised when Stage 3B would violate an immutable or isolation boundary."""


class DryRunProcessSpec(FrozenModel):
    executable: Path
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_version_output: str = Field(min_length=1)
    argv: Tuple[str, ...] = ()
    version_args: Tuple[str, ...] = ("--version",)
    timeout_seconds: float = Field(default=10, gt=0, le=120)
    allowed_environment: Tuple[str, ...] = ("PATH", "LANG", "LC_ALL")

    @model_validator(mode="after")
    def environment_is_minimal_and_secret_free(self) -> "DryRunProcessSpec":
        if len(set(self.allowed_environment)) != len(self.allowed_environment):
            raise ValueError("allowed_environment values must be unique")
        safe = {"PATH", "LANG", "LC_ALL", "TZ", "PYTHONHASHSEED", "ASE_DRY_RUN_LOG"}
        unknown = set(self.allowed_environment) - safe
        if unknown:
            raise ValueError(
                "dry-run Process environment is not on the allowlist: "
                + ", ".join(sorted(unknown))
            )
        secret_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
        if any(
            marker in name.upper()
            for name in self.allowed_environment
            for marker in secret_markers
        ):
            raise ValueError("dry-run Process cannot inherit Secret-like environment names")
        return self


class EvolutionDryRunSpec(FrozenModel):
    schema_version: Literal["ase/evolution-dry-run-spec/v1alpha1"]
    name: str = Field(min_length=1, max_length=120)
    execution_plan_directory: Path
    benchmark_workspace: Path
    release_manifest: Path
    optimizer_view: Path
    process: DryRunProcessSpec
    claim_limit: str = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> "EvolutionDryRunSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls.model_validate(payload)
            root = path.resolve(strict=True).parent

            def resolved(value: Path, *, directory: Optional[bool] = None) -> Path:
                candidate = value if value.is_absolute() else root / value
                if candidate.is_symlink():
                    raise EvolutionDryRunError("symbolic-link dry-run inputs are not allowed")
                result = candidate.expanduser().resolve(strict=True)
                if directory is True and not result.is_dir():
                    raise EvolutionDryRunError(f"expected directory: {result}")
                if directory is False and not result.is_file():
                    raise EvolutionDryRunError(f"expected regular file: {result}")
                return result

            process = spec.process.model_copy(
                update={"executable": resolved(spec.process.executable, directory=False)}
            )
            return spec.model_copy(
                update={
                    "execution_plan_directory": resolved(
                        spec.execution_plan_directory, directory=True
                    ),
                    "benchmark_workspace": resolved(spec.benchmark_workspace, directory=True),
                    "release_manifest": resolved(spec.release_manifest, directory=False),
                    "optimizer_view": resolved(spec.optimizer_view, directory=False),
                    "process": process,
                }
            )
        except EvolutionDryRunError:
            raise
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise EvolutionDryRunError(f"invalid evolution dry-run spec {path}: {exc}") from exc


class AdaptiveDatasetBinding(FrozenModel):
    split: AdaptiveStage
    dataset_version_id: UUID
    dataset_version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    independent_group_count: int = Field(ge=1)
    relative_path: str = Field(min_length=1)
    integrity_verified: Literal[True] = True
    content_access: Literal["adaptive_dataset_opened"] = "adaptive_dataset_opened"


class WithheldDatasetReceipt(FrozenModel):
    split: WithheldStage
    case_count: int = Field(ge=1)
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["WITHHELD"] = "WITHHELD"
    path_persisted: Literal[False] = False
    case_keys_persisted: Literal[False] = False
    content_accessed: Literal[False] = False


class DryRunProcessEvidence(FrozenModel):
    stage: AdaptiveStage
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_verified: Literal[True] = True
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: float = Field(ge=0)
    exit_code: Literal[0] = 0
    inherited_environment: Tuple[str, ...]
    raw_request_stored: Literal[False] = False
    raw_response_stored: Literal[False] = False
    stderr_stored: Literal[False] = False
    hidden_reasoning_stored: Literal[False] = False


class EvolutionDryRunReport(FrozenModel):
    schema_version: Literal["ase/evolution-dry-run-report/v1alpha1"] = (
        "ase/evolution-dry-run-report/v1alpha1"
    )
    dry_run_id: UUID
    name: str
    execution_plan_id: UUID
    execution_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_release_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    optimizer_view_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adaptive_bindings: Tuple[AdaptiveDatasetBinding, ...] = Field(
        min_length=2, max_length=2
    )
    withheld_receipts: Tuple[WithheldDatasetReceipt, ...] = Field(
        min_length=2, max_length=2
    )
    process_evidence: Tuple[DryRunProcessEvidence, ...] = Field(
        min_length=2, max_length=2
    )
    execution_sequence: Tuple[str, ...]
    status: Literal["AWAITING_REAL_AUTHORIZATION"] = "AWAITING_REAL_AUTHORIZATION"
    simulated: Literal[True] = True
    evidence_class: Literal["process_integration_dry_run"] = "process_integration_dry_run"
    real_calls_executed: Literal[False] = False
    agent_runs_executed: Literal[False] = False
    validation_confirm_content_accessed: Literal[False] = False
    locked_content_accessed: Literal[False] = False
    skill_improvement_claimed: Literal[False] = False
    claim_limit: str


class EvolutionDryRunManifest(FrozenModel):
    schema_version: Literal["ase/evolution-dry-run-manifest/v1alpha1"] = (
        "ase/evolution-dry-run-manifest/v1alpha1"
    )
    dry_run_id: UUID
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: Dict[str, str]
    immutable: Literal[True] = True
    simulated: Literal[True] = True
    real_calls_executed: Literal[False] = False
    locked_content_accessed: Literal[False] = False


class EvolutionDryRunResult(FrozenModel):
    report: EvolutionDryRunReport
    directory: Path
    manifest_path: Path
    report_path: Path


class EvolutionDryRunOrchestrator:
    """Bind adaptive datasets and rehearse orchestration without an Agent or model."""

    _ADAPTIVE: Tuple[AdaptiveStage, ...] = ("validation_search", "regression_dev")
    _WITHHELD: Tuple[WithheldStage, ...] = ("validation_confirm", "locked_test")

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def preflight(self, spec: EvolutionDryRunSpec) -> EvolutionDryRunReport:
        plan_result = RealEvolutionExecutionPlanner(
            spec.execution_plan_directory.parent.parent
        ).verify(spec.execution_plan_directory)
        plan = plan_result.plan
        release = OptimizationBenchmarkPublisher.load_release(spec.release_manifest)
        optimizer_payload = self._load_optimizer_view(spec.optimizer_view, release)
        executable_sha = self._verify_process(spec.process)
        bindings = tuple(
            self._bind_adaptive(spec, plan, release, split) for split in self._ADAPTIVE
        )
        receipts = tuple(self._withheld_receipt(plan, release, split) for split in self._WITHHELD)
        semantic = {
            "name": spec.name,
            "execution_plan_id": str(plan.plan_id),
            "execution_plan_manifest_sha256": hashlib.sha256(
                plan_result.manifest_path.read_bytes()
            ).hexdigest(),
            "benchmark_release_sha256": release.content_sha256,
            "optimizer_view_sha256": stable_sha256(optimizer_payload),
            "adaptive_bindings": [item.model_dump(mode="json") for item in bindings],
            "withheld_receipts": [item.model_dump(mode="json") for item in receipts],
            "process_executable_sha256": executable_sha,
            "process_version": spec.process.expected_version_output,
            "claim_limit": spec.claim_limit,
        }
        dry_run_id = uuid5(
            NAMESPACE_URL, "agentskill-eval:evolution-dry-run:" + stable_sha256(semantic)
        )
        return EvolutionDryRunReport(
            dry_run_id=dry_run_id,
            name=spec.name,
            execution_plan_id=plan.plan_id,
            execution_plan_sha256=hashlib.sha256(
                plan_result.plan_path.read_bytes()
            ).hexdigest(),
            benchmark_release_sha256=release.content_sha256,
            split_plan_sha256=release.plan_sha256,
            optimizer_view_sha256=stable_sha256(optimizer_payload),
            adaptive_bindings=bindings,
            withheld_receipts=receipts,
            process_evidence=tuple(
                self._rehearse(spec.process, dry_run_id, binding) for binding in bindings
            ),
            execution_sequence=(
                "bind_validation_search",
                "rehearse_validation_search",
                "bind_regression_dev",
                "rehearse_regression_dev",
                "hold_validation_confirm",
                "hold_locked_test",
                "await_real_authorization",
            ),
            claim_limit=spec.claim_limit,
        )

    def prepare(self, spec: EvolutionDryRunSpec) -> EvolutionDryRunResult:
        identity = self._identity(spec)
        directory = self.workspace / "evolution-dry-runs" / str(identity)
        if directory.exists():
            result = self.verify(directory)
            manifest = EvolutionDryRunManifest.model_validate_json(
                result.manifest_path.read_bytes()
            )
            if manifest.input_fingerprint != self._input_fingerprint(spec):
                raise EvolutionDryRunError("existing dry-run input fingerprint mismatch")
            return result
        report = self.preflight(spec)
        if report.dry_run_id != identity:
            raise EvolutionDryRunError("dry-run identity changed between binding and rehearsal")
        report_bytes = canonical_json(report.model_dump(mode="json")) + b"\n"
        bindings_bytes = canonical_json(
            [item.model_dump(mode="json") for item in report.adaptive_bindings]
        ) + b"\n"
        receipts_bytes = canonical_json(
            [item.model_dump(mode="json") for item in report.withheld_receipts]
        ) + b"\n"
        process_bytes = canonical_json(
            [item.model_dump(mode="json") for item in report.process_evidence]
        ) + b"\n"
        markdown_bytes = self._markdown(report).encode("utf-8")
        contents = {
            "dry-run-report.json": report_bytes,
            "adaptive-bindings.json": bindings_bytes,
            "withheld-receipts.json": receipts_bytes,
            "process-evidence.json": process_bytes,
            "dry-run-report.md": markdown_bytes,
        }
        artifacts = {
            name: hashlib.sha256(content).hexdigest() for name, content in contents.items()
        }
        manifest = EvolutionDryRunManifest(
            dry_run_id=report.dry_run_id,
            report_sha256=artifacts["dry-run-report.json"],
            input_fingerprint=self._input_fingerprint(spec),
            artifacts=artifacts,
        )
        directory.mkdir(parents=True, exist_ok=False)
        for name, content in contents.items():
            self.writer.write(directory / name, content)
        self.writer.write(
            directory / "dry-run-manifest.json",
            canonical_json(manifest.model_dump(mode="json")) + b"\n",
        )
        return self.verify(directory)

    def verify(self, directory: Path) -> EvolutionDryRunResult:
        root = directory.resolve(strict=True)
        try:
            manifest_path = root / "dry-run-manifest.json"
            manifest = EvolutionDryRunManifest.model_validate_json(manifest_path.read_bytes())
            expected_artifacts = {
                "dry-run-report.json",
                "adaptive-bindings.json",
                "withheld-receipts.json",
                "process-evidence.json",
                "dry-run-report.md",
            }
            if set(manifest.artifacts) != expected_artifacts:
                raise EvolutionDryRunError("dry-run artifact set is incomplete or unsafe")
            for name, expected in manifest.artifacts.items():
                path = root / name
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise EvolutionDryRunError(f"dry-run artifact mismatch: {name}")
            report_path = root / "dry-run-report.json"
            report = EvolutionDryRunReport.model_validate_json(report_path.read_bytes())
        except EvolutionDryRunError:
            raise
        except (OSError, ValueError) as exc:
            raise EvolutionDryRunError(f"invalid evolution dry-run {directory}: {exc}") from exc
        if report.dry_run_id != manifest.dry_run_id:
            raise EvolutionDryRunError("dry-run manifest identity mismatch")
        if hashlib.sha256(report_path.read_bytes()).hexdigest() != manifest.report_sha256:
            raise EvolutionDryRunError("dry-run report digest mismatch")
        if (
            report.real_calls_executed
            or report.agent_runs_executed
            or report.validation_confirm_content_accessed
            or report.locked_content_accessed
            or report.skill_improvement_claimed
        ):
            raise EvolutionDryRunError("dry-run report claims forbidden execution or access")
        if {item.stage for item in report.process_evidence} != set(self._ADAPTIVE):
            raise EvolutionDryRunError("Process rehearsal stage set is not adaptive-only")
        return EvolutionDryRunResult(
            report=report,
            directory=root,
            manifest_path=manifest_path,
            report_path=report_path,
        )

    def _identity(self, spec: EvolutionDryRunSpec) -> UUID:
        plan = RealEvolutionExecutionPlanner(
            spec.execution_plan_directory.parent.parent
        ).verify(spec.execution_plan_directory)
        release = OptimizationBenchmarkPublisher.load_release(spec.release_manifest)
        optimizer_payload = self._load_optimizer_view(spec.optimizer_view, release)
        executable_sha = self._verify_process(spec.process)
        bindings = tuple(
            self._bind_adaptive(spec, plan.plan, release, split) for split in self._ADAPTIVE
        )
        receipts = tuple(
            self._withheld_receipt(plan.plan, release, split) for split in self._WITHHELD
        )
        semantic = {
            "name": spec.name,
            "execution_plan_id": str(plan.plan.plan_id),
            "execution_plan_manifest_sha256": hashlib.sha256(
                plan.manifest_path.read_bytes()
            ).hexdigest(),
            "benchmark_release_sha256": release.content_sha256,
            "optimizer_view_sha256": stable_sha256(optimizer_payload),
            "adaptive_bindings": [item.model_dump(mode="json") for item in bindings],
            "withheld_receipts": [item.model_dump(mode="json") for item in receipts],
            "process_executable_sha256": executable_sha,
            "process_version": spec.process.expected_version_output,
            "claim_limit": spec.claim_limit,
        }
        return uuid5(NAMESPACE_URL, "agentskill-eval:evolution-dry-run:" + stable_sha256(semantic))

    def _bind_adaptive(
        self,
        spec: EvolutionDryRunSpec,
        plan: EvolutionExecutionPlan,
        release: OptimizationBenchmarkRelease,
        split: AdaptiveStage,
    ) -> AdaptiveDatasetBinding:
        reference = self._reference(release, split)
        descriptor = getattr(plan.datasets, split)
        self._match_descriptor(descriptor, reference, release)
        root = (spec.benchmark_workspace / reference.relative_path).resolve(strict=True)
        if not root.is_relative_to(spec.benchmark_workspace):
            raise EvolutionDryRunError("adaptive DatasetVersion path escapes benchmark workspace")
        loaded = DatasetLoader().load(root)
        version = loaded.dataset_version
        if version is None:
            raise EvolutionDryRunError(f"adaptive split {split} is not a published DatasetVersion")
        BenchmarkStore.assert_dataset_version_integrity(version, root)
        if (
            version.id != reference.dataset_version_id
            or version.content_sha256 != reference.dataset_content_sha256
            or len(loaded.cases) != reference.case_count
            or loaded.independence_groups != reference.independence_groups
        ):
            raise EvolutionDryRunError(f"adaptive DatasetVersion reference mismatch: {split}")
        return AdaptiveDatasetBinding(
            split=split,
            dataset_version_id=reference.dataset_version_id,
            dataset_version_sha256=reference.dataset_content_sha256,
            split_plan_sha256=release.plan_sha256,
            case_count=reference.case_count,
            independent_group_count=len(reference.independence_groups),
            relative_path=reference.relative_path,
        )

    def _withheld_receipt(
        self,
        plan: EvolutionExecutionPlan,
        release: OptimizationBenchmarkRelease,
        split: WithheldStage,
    ) -> WithheldDatasetReceipt:
        reference = self._reference(release, split)
        descriptor = getattr(plan.datasets, split)
        self._match_descriptor(descriptor, reference, release)
        return WithheldDatasetReceipt(
            split=split,
            case_count=reference.case_count,
            receipt_sha256=stable_sha256(
                {
                    "release": release.content_sha256,
                    "split": split,
                    "dataset": reference.dataset_content_sha256,
                }
            ),
        )

    @staticmethod
    def _match_descriptor(
        descriptor: DatasetPlanDescriptor,
        reference: SplitDatasetReference,
        release: OptimizationBenchmarkRelease,
    ) -> None:
        if (
            descriptor.dataset_version_sha256 != reference.dataset_content_sha256
            or descriptor.split_plan_sha256 != release.plan_sha256
            or descriptor.case_count != reference.case_count
            or descriptor.independent_group_count != len(reference.independence_groups)
        ):
            raise EvolutionDryRunError(
                f"execution plan DatasetVersion mismatch: {descriptor.split}"
            )

    @staticmethod
    def _reference(
        release: OptimizationBenchmarkRelease, split: str
    ) -> SplitDatasetReference:
        matches = [item for item in release.splits if item.split.value == split]
        if len(matches) != 1:
            raise EvolutionDryRunError(f"release does not contain split exactly once: {split}")
        return matches[0]

    @staticmethod
    def _load_optimizer_view(
        path: Path, release: OptimizationBenchmarkRelease
    ) -> Mapping[str, object]:
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvolutionDryRunError(f"invalid optimizer view {path}: {exc}") from exc
        if not isinstance(decoded, dict):
            raise EvolutionDryRunError("optimizer view root must be an object")
        payload = cast(Dict[str, object], decoded)
        expected = OptimizationBenchmarkPublisher.optimizer_view(release)
        if payload != expected:
            raise EvolutionDryRunError("optimizer view does not exactly match the frozen release")
        serialized = canonical_json(payload)
        for forbidden in (b'"relative_path"', b'"candidate_keys"'):
            withheld = canonical_json(payload.get("withheld_splits", []))
            if forbidden in withheld:
                raise EvolutionDryRunError("optimizer view leaks withheld DatasetVersion inputs")
        if b'"locked_test_accessed":true' in serialized:
            raise EvolutionDryRunError("optimizer view claims locked-test access")
        return payload

    @staticmethod
    def _verify_process(spec: DryRunProcessSpec) -> str:
        expanded = spec.executable.expanduser()
        if expanded.is_symlink():
            raise EvolutionDryRunError("dry-run Process executable must not be a symlink")
        executable = expanded.resolve(strict=True)
        if not executable.is_file():
            raise EvolutionDryRunError("dry-run Process executable must be a regular file")
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        if digest != spec.expected_sha256:
            raise EvolutionDryRunError("dry-run Process executable SHA-256 mismatch")
        stdout, _duration = EvolutionDryRunOrchestrator._communicate(spec, b"", version=True)
        if stdout.decode("utf-8", errors="replace").strip() != spec.expected_version_output:
            raise EvolutionDryRunError("dry-run Process version mismatch")
        return digest

    def _rehearse(
        self,
        spec: DryRunProcessSpec,
        dry_run_id: UUID,
        binding: AdaptiveDatasetBinding,
    ) -> DryRunProcessEvidence:
        request = {
            "schema_version": "ase/evolution-dry-run-process-request/v1alpha1",
            "dry_run_id": str(dry_run_id),
            "stage": binding.split,
            "dataset_version_sha256": binding.dataset_version_sha256,
            "split_plan_sha256": binding.split_plan_sha256,
            "case_count": binding.case_count,
            "independent_group_count": binding.independent_group_count,
            "operation": "metadata_only_orchestration_rehearsal",
        }
        request_bytes = canonical_json(request) + b"\n"
        stdout, duration_ms = self._communicate(spec, request_bytes)
        try:
            response = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvolutionDryRunError("dry-run Process returned invalid JSON") from exc
        expected = {
            "schema_version": "ase/evolution-dry-run-process-response/v1alpha1",
            "dry_run_id": str(dry_run_id),
            "stage": binding.split,
            "dataset_version_sha256": binding.dataset_version_sha256,
            "accepted": True,
        }
        if response != expected:
            raise EvolutionDryRunError("dry-run Process response does not match its request")
        if hashlib.sha256(spec.executable.read_bytes()).hexdigest() != spec.expected_sha256:
            raise EvolutionDryRunError("dry-run Process executable changed during rehearsal")
        return DryRunProcessEvidence(
            stage=binding.split,
            executable_sha256=spec.expected_sha256,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            response_sha256=stable_sha256(response),
            duration_ms=duration_ms,
            inherited_environment=tuple(
                name for name in spec.allowed_environment if name in os.environ
            ),
        )

    @staticmethod
    def _communicate(
        spec: DryRunProcessSpec, stdin: bytes, *, version: bool = False
    ) -> Tuple[bytes, float]:
        args = [str(spec.executable), *spec.argv]
        if version:
            args.extend(spec.version_args)
        environment = {
            name: os.environ[name] for name in spec.allowed_environment if name in os.environ
        }
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
            stdout, _stderr = process.communicate(stdin, timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise EvolutionDryRunError("dry-run Process timed out") from exc
        except OSError as exc:
            raise EvolutionDryRunError(f"dry-run Process could not start: {exc}") from exc
        except BaseException:
            if "process" in locals() and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise
        if process.returncode != 0:
            raise EvolutionDryRunError(f"dry-run Process exited with code {process.returncode}")
        if len(stdout) > 100_000:
            raise EvolutionDryRunError("dry-run Process response is too large")
        return stdout, (time.monotonic() - started) * 1000

    @staticmethod
    def _input_fingerprint(spec: EvolutionDryRunSpec) -> str:
        return stable_sha256(
            {
                "execution_plan_manifest": hashlib.sha256(
                    (spec.execution_plan_directory / "execution-plan-manifest.json").read_bytes()
                ).hexdigest(),
                "release_manifest": hashlib.sha256(spec.release_manifest.read_bytes()).hexdigest(),
                "optimizer_view": hashlib.sha256(spec.optimizer_view.read_bytes()).hexdigest(),
                "process_executable": spec.process.expected_sha256,
                "claim_limit": spec.claim_limit,
            }
        )

    @staticmethod
    def _markdown(report: EvolutionDryRunReport) -> str:
        adaptive = "\n".join(
            f"| {item.split} | REHEARSED | {item.case_count} | `{item.dataset_version_sha256}` |"
            for item in report.adaptive_bindings
        )
        withheld = "\n".join(
            f"| {item.split} | WITHHELD | {item.case_count} | `{item.receipt_sha256}` |"
            for item in report.withheld_receipts
        )
        return f"""# Real Evolution Dry-Run Orchestration

- Dry run: `{report.dry_run_id}`
- Execution plan: `{report.execution_plan_id}`
- Status: **{report.status}**
- Evidence: `simulated=true`, `{report.evidence_class}`
- Real model calls: **false**
- Agent runs: **false**
- Locked content accessed: **false**

| Adaptive stage | Status | Cases | DatasetVersion SHA-256 |
|---|---|---:|---|
{adaptive}

| Protected stage | Status | Cases | Withheld receipt |
|---|---|---:|---|
{withheld}

## Claim limit

{report.claim_limit}
"""
