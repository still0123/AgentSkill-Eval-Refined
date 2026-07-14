"""Budgeted, idempotent real-Agent paired experiment orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue

from agentskill_eval_benchmark_gen import LoadedDataset
from agentskill_eval_contracts import (
    AgentSnapshot,
    EnvironmentFingerprint,
    ExperimentManifest,
    ExperimentStatus,
    ExperimentVariant,
    PriceSnapshot,
    RealAttemptEvidence,
    RealEvidenceClass,
    RealEvidenceRunManifest,
    RealEvidenceStatus,
    RealExperimentReport,
    RealPreflightReport,
    RealRunMode,
    RunnerSnapshot,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
    VariantReference,
    VariantRole,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment import (
    AnalysisConfig,
    ExperimentAnalyzer,
    LocalExperimentExecutor,
    LocalExperimentPlanner,
    LocalExperimentStore,
    ReplayBundleWriter,
    StaticReportWriter,
    VariantRuntimeSpec,
)
from agentskill_eval_experiment.storage import AtomicFileWriter, ExperimentLayout, load_model
from agentskill_eval_experiment.storage.manifests import model_bytes
from agentskill_eval_real_evidence.preflight import RealEvidencePreflight
from agentskill_eval_real_evidence.reporting import RealEvidenceReportWriter
from agentskill_eval_real_evidence.spec import RealAgentEvidenceSpec
from agentskill_eval_runner_adapters import (
    RunnerAdapter,
    RunnerRequest,
    RunnerResult,
    SkillUpRunnerAdapter,
    ValidationReport,
)
from agentskill_eval_runner_adapters.contracts import TraceEventSink, null_event_sink


class RealEvidenceError(RuntimeError):
    """Raised when authorization, evidence boundaries, or budgets are violated."""


@dataclass(frozen=True)
class RealEvidenceResult:
    manifest: RealEvidenceRunManifest
    report: Optional[RealExperimentReport]
    report_json: Optional[Path]
    report_html: Optional[Path]
    replay_bundle: Optional[Path]


class RealEvidenceStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def experiment_dir(self, experiment_id: UUID) -> Path:
        return self.workspace / "experiments" / str(experiment_id)

    def preflight_path(self, experiment_id: UUID) -> Path:
        return self.experiment_dir(experiment_id) / "real-preflight.json"

    def run_path(self, experiment_id: UUID) -> Path:
        return self.experiment_dir(experiment_id) / "real-evidence-run.json"

    def report_json(self, experiment_id: UUID) -> Path:
        return self.experiment_dir(experiment_id) / "reports" / "real-experiment-report.json"

    def report_html(self, experiment_id: UUID) -> Path:
        return self.experiment_dir(experiment_id) / "reports" / "real-experiment-report.html"

    def save_preflight(self, experiment_id: UUID, report: RealPreflightReport) -> None:
        path = self.preflight_path(experiment_id)
        if path.exists():
            existing = load_model(path.read_bytes(), RealPreflightReport)
            if existing.config_sha256 != report.config_sha256:
                raise RealEvidenceError("persisted preflight belongs to another configuration")
            return
        self.writer.write(path, model_bytes(report))

    def load_preflight(self, experiment_id: UUID) -> RealPreflightReport:
        return load_model(self.preflight_path(experiment_id).read_bytes(), RealPreflightReport)

    def save_run(self, manifest: RealEvidenceRunManifest) -> None:
        self.writer.write(self.run_path(manifest.experiment_id), model_bytes(manifest))

    def load_run(self, experiment_id: UUID) -> RealEvidenceRunManifest:
        return load_model(self.run_path(experiment_id).read_bytes(), RealEvidenceRunManifest)

    def save_attempt_evidence(self, evidence: RealAttemptEvidence, attempt_no: int) -> Path:
        layout = ExperimentLayout(self.workspace, evidence.experiment_id)
        path = layout.attempt_root(evidence.run_id, attempt_no) / "real-attempt-evidence.json"
        if path.exists():
            existing = load_model(path.read_bytes(), RealAttemptEvidence)
            if existing != evidence:
                raise RealEvidenceError("real Attempt evidence is immutable")
            return path
        self.writer.write(path, model_bytes(evidence))
        return path

    def load_report(self, experiment_id: UUID) -> RealExperimentReport:
        return load_model(self.report_json(experiment_id).read_bytes(), RealExperimentReport)


class CostingRunnerAdapter:
    """Preserve provider observations and estimate cost only when Runner omits it."""

    def __init__(self, delegate: RunnerAdapter, spec: RealAgentEvidenceSpec) -> None:
        self.delegate = delegate
        self.spec = spec

    @property
    def compatibility(self):  # type: ignore[no-untyped-def]
        return self.delegate.compatibility

    async def validate(self, request: RunnerRequest) -> ValidationReport:
        return await self.delegate.validate(request)

    async def execute(
        self, request: RunnerRequest, event_sink: TraceEventSink = null_event_sink
    ) -> RunnerResult:
        result = await self.delegate.execute(request, event_sink)
        if result.cost_microusd is not None:
            return result
        if result.input_tokens is None or result.output_tokens is None:
            return result
        price = self.spec.pricing
        cached = min(result.cached_input_tokens or 0, result.input_tokens)
        cache_miss = result.input_tokens - cached
        numerator = (
            cache_miss * price.input_microusd_per_million_tokens
            + cached * price.cache_hit_rate_microusd
            + result.output_tokens * price.output_microusd_per_million_tokens
        )
        return replace(result, cost_microusd=(numerator + 999_999) // 1_000_000)

    async def cancel(self, execution_id: str) -> bool:
        return await self.delegate.cancel(execution_id)


class RealAgentEvidenceRunner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.store = RealEvidenceStore(self.workspace)
        self.experiment_store = LocalExperimentStore(self.workspace)

    def preflight(self, spec: RealAgentEvidenceSpec) -> RealPreflightReport:
        report, _dataset = RealEvidencePreflight().check(spec)
        return report

    async def run(
        self,
        spec: RealAgentEvidenceSpec,
        mode: RealRunMode,
        *,
        confirm_real_run: bool,
        max_cost_microusd: int,
        max_agent_runs: int,
        allow_process_integration: bool = False,
        progress_sink: Optional[Callable[..., None]] = None,
    ) -> RealEvidenceResult:
        if max_cost_microusd < 1 or max_agent_runs < 1:
            raise RealEvidenceError("positive cost and Agent Run limits are required")
        observed = spec.evidence_class == RealEvidenceClass.OBSERVED_AGENT
        if observed and not confirm_real_run:
            raise RealEvidenceError("observed Agent execution requires explicit confirmation")
        if not observed and not allow_process_integration:
            raise RealEvidenceError("process-integration fixtures cannot run through real CLI")

        preflight, dataset = RealEvidencePreflight().check(spec)
        repeats = 1 if mode == RealRunMode.SMOKE else spec.protocol.evidence_repeats
        planned_runs = len(spec.case_ids) * 2 * repeats
        if planned_runs > max_agent_runs:
            raise RealEvidenceError(
                f"plan requires {planned_runs} Agent Runs but authorization allows {max_agent_runs}"
            )
        estimated_total = planned_runs * preflight.estimated_cost_per_run_microusd
        if estimated_total > max_cost_microusd:
            raise RealEvidenceError(
                f"estimated cost {estimated_total} exceeds authorization {max_cost_microusd}"
            )

        experiment_id = uuid5(
            NAMESPACE_URL,
            f"ase-real-evidence:{preflight.config_sha256}:{mode.value}",
        )
        existing_path = self.store.run_path(experiment_id)
        if existing_path.exists():
            existing = self.store.load_run(experiment_id)
            if existing.status == RealEvidenceStatus.COMPLETED:
                return self._load_completed(existing)
            raise RealEvidenceError(
                f"existing real-evidence experiment is {existing.status.value}; "
                "automatic paid resume is forbidden"
            )
        self.store.save_preflight(experiment_id, preflight)
        started = datetime.now(timezone.utc)
        claim = self._claim_limit(spec, mode)
        manifest = RealEvidenceRunManifest(
            experiment_id=experiment_id,
            mode=mode,
            status=RealEvidenceStatus.RUNNING,
            config_sha256=preflight.config_sha256,
            preflight_sha256=stable_sha256(preflight.model_dump(mode="json")),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            provider=spec.agent.provider,
            model=spec.agent.model,
            real_run_confirmed=observed and confirm_real_run,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
            planned_runs=planned_runs,
            completed_runs=0,
            invalid_runs=0,
            observed_or_reserved_cost_microusd=0,
            started_at=started,
            claim_limit=claim,
        )
        self.store.save_run(manifest)
        try:
            result = await self._execute(
                spec,
                mode,
                preflight,
                dataset,
                manifest,
                repeats,
                progress_sink,
            )
        except BaseException as exc:
            cancelled = isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
            failed = manifest.model_copy(
                update={
                    "status": (
                        RealEvidenceStatus.CANCELLED if cancelled else RealEvidenceStatus.FAILED
                    ),
                    "completed_at": datetime.now(timezone.utc),
                }
            )
            self.store.save_run(failed)
            raise
        return result

    async def _execute(
        self,
        spec: RealAgentEvidenceSpec,
        mode: RealRunMode,
        preflight: RealPreflightReport,
        dataset: LoadedDataset,
        manifest: RealEvidenceRunManifest,
        repeats: int,
        progress_sink: Optional[Callable[..., None]],
    ) -> RealEvidenceResult:
        variants, runtimes, experiment = self._experiment_inputs(
            spec, mode, preflight, dataset, manifest.experiment_id
        )
        selected_ids = set(spec.case_ids)
        cases = tuple(
            item.execution_spec(dataset.dataset_id, dataset.root / "evals")
            for item in dataset.cases
            if item.metadata.case_id in selected_ids
        )
        planner = LocalExperimentPlanner(self.experiment_store)
        plan = planner.build(
            experiment,
            variants,
            runtimes,
            cases,
            repeats=repeats,
            random_seed=spec.protocol.random_seed,
            max_attempts=1,
        )
        planner.persist(plan)
        adapter = CostingRunnerAdapter(
            SkillUpRunnerAdapter(
                spec.runner.path,
                expected_sha256=spec.runner.expected_sha256,
                agent_executable=spec.agent.path,
            ),
            spec,
        )

        def observed_or_reserved_cost() -> int:
            total = 0
            for run in self.experiment_store.list_runs(experiment.id):
                measurement = self.experiment_store.load_selected_measurement(experiment.id, run)
                if measurement is not None:
                    total += (
                        measurement.cost_microusd
                        if measurement.cost_microusd is not None
                        else preflight.estimated_cost_per_run_microusd
                    )
            return total

        def run_gate() -> bool:
            return (
                observed_or_reserved_cost() + preflight.estimated_cost_per_run_microusd
                <= manifest.max_cost_microusd
            )

        execution = await LocalExperimentExecutor(
            self.experiment_store,
            adapter,
            worker_id="real-evidence-local-worker",
            progress_sink=progress_sink,
            run_start_gate=run_gate,
        ).execute(plan)
        cost = observed_or_reserved_cost()
        now = datetime.now(timezone.utc)
        if execution.cancelled:
            cancelled = manifest.model_copy(
                update={
                    "status": RealEvidenceStatus.CANCELLED,
                    "completed_runs": execution.completed_runs,
                    "invalid_runs": execution.invalid_runs,
                    "observed_or_reserved_cost_microusd": cost,
                    "completed_at": now,
                }
            )
            self.store.save_run(cancelled)
            return RealEvidenceResult(cancelled, None, None, None, None)
        if execution.budget_exhausted:
            exhausted = manifest.model_copy(
                update={
                    "status": RealEvidenceStatus.BUDGET_EXHAUSTED,
                    "completed_runs": execution.completed_runs,
                    "invalid_runs": execution.invalid_runs,
                    "observed_or_reserved_cost_microusd": cost,
                    "completed_at": now,
                }
            )
            self.store.save_run(exhausted)
            return RealEvidenceResult(exhausted, None, None, None, None)
        if execution.completed_runs + execution.invalid_runs != manifest.planned_runs:
            raise RealEvidenceError("execution ended without a complete terminal Run set")

        completed = manifest.model_copy(
            update={
                "status": RealEvidenceStatus.COMPLETED,
                "completed_runs": execution.completed_runs,
                "invalid_runs": execution.invalid_runs,
                "observed_or_reserved_cost_microusd": cost,
                "completed_at": now,
            }
        )
        self.store.save_run(completed)
        attempt_paths = self._persist_attempt_evidence(spec, preflight, completed)
        statistics = ExperimentAnalyzer(self.experiment_store).analyze(
            experiment.id,
            AnalysisConfig(
                control_variant_id=variants[0].id,
                treatment_variant_id=variants[1].id,
                bootstrap_resamples=spec.protocol.bootstrap_resamples,
                bootstrap_seed=spec.protocol.random_seed,
                min_independent_groups=spec.protocol.min_independent_groups,
            ),
        )
        StaticReportWriter(self.experiment_store).write(experiment.id, statistics)
        evidence_source = (
            ExperimentLayout(self.workspace, experiment.id).reports / "real-source.json"
        )
        self.store.writer.write(
            evidence_source,
            canonical_json(
                {
                    "run": completed.model_dump(mode="json"),
                    "statistics": statistics.model_dump(mode="json"),
                    "attempt_evidence_paths": attempt_paths,
                }
            ),
        )
        bundle_path = self.workspace / "real-evidence-bundles" / f"{experiment.id}.tar"
        bundle = ReplayBundleWriter(self.experiment_store).write(experiment.id, bundle_path)
        report, json_path, html_path = RealEvidenceReportWriter(
            self.experiment_store, self.store
        ).write(
            completed,
            preflight,
            variants[0],
            variants[1],
            statistics,
            tuple(attempt_paths),
            bundle.path,
        )
        return RealEvidenceResult(completed, report, json_path, html_path, bundle.path)

    def _experiment_inputs(
        self,
        spec: RealAgentEvidenceSpec,
        mode: RealRunMode,
        preflight: RealPreflightReport,
        dataset: LoadedDataset,
        experiment_id: UUID,
    ) -> Tuple[
        Tuple[ExperimentVariant, ExperimentVariant],
        Tuple[VariantRuntimeSpec, VariantRuntimeSpec],
        ExperimentManifest,
    ]:
        runner = RunnerSnapshot(
            name=spec.runner.name,
            version=spec.runner.expected_version,
            binary_sha256=preflight.runner.sha256,
            config={
                "simulated": spec.simulated,
                "evidence_class": spec.evidence_class.value,
                "agent_executable_sha256": preflight.agent.sha256,
            },
        )
        generation: dict[str, JsonValue] = {
            "provider": spec.agent.provider,
            "engine_provider": spec.agent.resolved_engine_provider,
            "base_url": spec.agent.base_url,
            "home_config_sha256": stable_sha256(spec.agent.home_config_files),
            "temperature": spec.agent.temperature,
            "seed": spec.agent.seed,
            "max_input_tokens": spec.agent.max_input_tokens,
            "max_output_tokens": spec.agent.max_output_tokens,
            "max_turns": spec.agent.max_turns,
            "max_tool_calls": spec.agent.max_tool_calls,
            "timeout_seconds": spec.agent.timeout_seconds,
        }
        agent = AgentSnapshot(
            engine=spec.agent.engine,
            engine_version=spec.agent.engine_version,
            model=spec.agent.model,
            generation_parameters=generation,
        )
        tools = ToolSnapshot(
            definitions_sha256=stable_sha256(spec.agent.tool_capabilities),
            config={"capabilities": list(spec.agent.tool_capabilities)},
        )
        sandbox = SandboxSnapshot(
            profile=spec.sandbox_profile,
            network_policy=spec.network_policy,
            resource_limits={
                "timeout_seconds": spec.agent.timeout_seconds,
                "max_turns": spec.agent.max_turns,
                "max_tool_calls": spec.agent.max_tool_calls,
            },
        )
        rates: dict[str, JsonValue] = {
            "input_microusd_per_million_tokens": (spec.pricing.input_microusd_per_million_tokens),
            "input_cache_hit_microusd_per_million_tokens": (
                spec.pricing.cache_hit_rate_microusd
            ),
            "output_microusd_per_million_tokens": (spec.pricing.output_microusd_per_million_tokens),
        }
        price = PriceSnapshot(
            table_sha256=stable_sha256(rates),
            rates=rates,
        )
        baseline = ExperimentVariant(
            id=uuid5(experiment_id, "variant:without-skill"),
            experiment_id=experiment_id,
            name="without-skill",
            role=VariantRole.BASELINE,
            runner_snapshot=runner,
            agent_snapshot=agent,
            tool_snapshot=tools,
            sandbox_snapshot=sandbox,
            price_snapshot=price,
        )
        treatment = ExperimentVariant(
            id=uuid5(experiment_id, "variant:with-skill"),
            experiment_id=experiment_id,
            name="with-skill",
            role=VariantRole.TREATMENT,
            runner_snapshot=runner,
            agent_snapshot=agent,
            skill_snapshot=SkillSnapshot(
                skill_id=uuid5(NAMESPACE_URL, "agentskill-eval:skill:python-bug-fix"),
                version_id=uuid5(NAMESPACE_URL, f"skill:{preflight.skill_sha256}"),
                name="python-bug-fix-v1",
                version="1.0.0",
                content_sha256=preflight.skill_sha256,
                injection_mode="skill-up-native-install",
            ),
            tool_snapshot=tools,
            sandbox_snapshot=sandbox,
            price_snapshot=price,
        )
        engine_model = {
            "provider": spec.agent.resolved_engine_provider,
            "name": spec.agent.model,
            "temperature": spec.agent.temperature,
            "max_input_tokens": spec.agent.max_input_tokens,
            "max_output_tokens": spec.agent.max_output_tokens,
        }
        if spec.agent.base_url is not None:
            engine_model["base_url"] = spec.agent.base_url
        if spec.agent.seed is not None:
            engine_model["seed"] = spec.agent.seed
        engine = {"name": spec.agent.engine, "model": engine_model}
        secrets = RealEvidencePreflight.secret_values(spec.agent.secret_env_names)
        runtimes = (
            VariantRuntimeSpec(
                variant_id=baseline.id,
                engine=engine,
                environment={"type": "none"},
                timeout_seconds=spec.agent.timeout_seconds,
                max_turns=spec.agent.max_turns,
                agent_home_files=spec.agent.home_config_files,
                secret_env=secrets,
            ),
            VariantRuntimeSpec(
                variant_id=treatment.id,
                engine=engine,
                environment={"type": "none"},
                skill_path=spec.skill_path.resolve(strict=True),
                timeout_seconds=spec.agent.timeout_seconds,
                max_turns=spec.agent.max_turns,
                agent_home_files=spec.agent.home_config_files,
                secret_env=secrets,
            ),
        )
        existing = self.store.experiment_dir(experiment_id) / "experiment.json"
        if existing.exists():
            experiment = self.experiment_store.load_experiment(experiment_id)
        else:
            experiment = ExperimentManifest(
                id=experiment_id,
                name=f"{spec.name} ({mode.value})",
                code_revision=self._code_revision(),
                dataset_version_id=dataset.dataset_id,
                dataset_sha256=dataset.dataset_sha256,
                protocol_snapshot={
                    "simulated": spec.simulated,
                    "evidence_class": spec.evidence_class.value,
                    "real_run_confirmed": spec.evidence_class == RealEvidenceClass.OBSERVED_AGENT,
                    "mode": mode.value,
                    "repeats": 1 if mode == RealRunMode.SMOKE else spec.protocol.evidence_repeats,
                    "random_seed": spec.protocol.random_seed,
                    "claim_limit": self._claim_limit(spec, mode),
                },
                statistics_plan={
                    "primary": "assignment_based_absolute_gain",
                    "weighting": "equal_independence_group",
                    "bootstrap_resamples": spec.protocol.bootstrap_resamples,
                    "min_independent_groups": spec.protocol.min_independent_groups,
                },
                budget_snapshot={
                    "max_runs": len(spec.case_ids)
                    * 2
                    * (1 if mode == RealRunMode.SMOKE else spec.protocol.evidence_repeats),
                    "estimated_cost_per_run_microusd": (
                        spec.pricing.estimated_cost_per_run_microusd
                    ),
                },
                variants=tuple(
                    VariantReference(
                        variant_id=item.id,
                        variant_sha256=item.variant_sha256,
                        manifest_path=f"variants/{item.id}.json",
                    )
                    for item in (baseline, treatment)
                ),
                status=ExperimentStatus.FROZEN,
            )
        return (baseline, treatment), runtimes, experiment

    def _persist_attempt_evidence(
        self,
        spec: RealAgentEvidenceSpec,
        preflight: RealPreflightReport,
        manifest: RealEvidenceRunManifest,
    ) -> list[str]:
        paths = []
        environment = EnvironmentFingerprint(
            platform_revision=self._code_revision(),
            runner_version=spec.runner.expected_version,
            runner_binary_sha256=preflight.runner.sha256,
            agent_engine=spec.agent.engine,
            agent_version=spec.agent.engine_version,
            provider=spec.agent.provider,
            model_revision=spec.agent.model,
            runtime_dependencies={
                "python": platform.python_version(),
                "agent_executable_sha256": preflight.agent.sha256,
            },
            unavailable_reasons={
                "provider_request_id": "skill-up v0.5.0 result does not expose it",
                "region": "provider region is not exposed by the pinned Runner",
                "image_digest": "runner_default does not expose a container image digest",
            },
        )
        for run in self.experiment_store.list_runs(manifest.experiment_id):
            attempt = self.experiment_store.load_selected_attempt(manifest.experiment_id, run)
            if attempt is None:
                raise RealEvidenceError(f"terminal run {run.id} has no selected Attempt")
            trace = self.experiment_store.load_trace_manifest(
                manifest.experiment_id, run.id, attempt.attempt_no
            )
            unavailable = tuple(
                item.name for item in trace.capabilities if item.availability.value == "unavailable"
            )
            final_hash = self._final_message_hash(
                manifest.experiment_id, run.id, attempt.attempt_no
            )
            evidence = RealAttemptEvidence(
                experiment_id=manifest.experiment_id,
                run_id=run.id,
                attempt_id=attempt.id,
                simulated=manifest.simulated,
                evidence_class=manifest.evidence_class,
                provider=manifest.provider,
                model=manifest.model,
                real_run_confirmed=manifest.real_run_confirmed,
                environment=environment,
                final_message_sha256=final_hash,
                capability_unavailable=unavailable,
                claim_limit=manifest.claim_limit,
            )
            path = self.store.save_attempt_evidence(evidence, attempt.attempt_no)
            paths.append(
                path.relative_to(self.store.experiment_dir(manifest.experiment_id)).as_posix()
            )
        return sorted(paths)

    def _final_message_hash(
        self, experiment_id: UUID, run_id: UUID, attempt_no: int
    ) -> Optional[str]:
        path = ExperimentLayout(self.workspace, experiment_id).raw_runner(run_id, attempt_no)
        result_path = path / "result.json"
        if not result_path.is_file():
            return None
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            cases = payload.get("case_results", [])
            response = cases[0].get("response") if cases and isinstance(cases[0], dict) else None
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
        return hashlib.sha256(response.encode()).hexdigest() if isinstance(response, str) else None

    def _load_completed(self, manifest: RealEvidenceRunManifest) -> RealEvidenceResult:
        report = self.store.load_report(manifest.experiment_id)
        bundle = self.workspace / "real-evidence-bundles" / f"{manifest.experiment_id}.tar"
        ReplayBundleWriter.verify(bundle)
        return RealEvidenceResult(
            manifest,
            report,
            self.store.report_json(manifest.experiment_id),
            self.store.report_html(manifest.experiment_id),
            bundle,
        )

    @staticmethod
    def _claim_limit(spec: RealAgentEvidenceSpec, mode: RealRunMode) -> str:
        if spec.simulated:
            return "Process integration fixture only; not Agent or Skill performance evidence."
        if mode == RealRunMode.SMOKE:
            return "Observed-Agent smoke evidence for two cases; validates the chain only."
        return (
            "Observed-Agent descriptive evidence for two independent cases; "
            "does not support general performance claims."
        )

    @staticmethod
    def _code_revision() -> str:
        try:
            return subprocess.run(
                ("git", "rev-parse", "HEAD"),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown-local-revision"
