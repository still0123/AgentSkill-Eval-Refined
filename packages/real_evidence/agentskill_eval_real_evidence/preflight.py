"""No-cost validation and immutable snapshots for real Agent experiments."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from agentskill_eval_benchmark_gen import DatasetLoader, LoadedCase, LoadedDataset
from agentskill_eval_contracts import ExecutableSnapshot, RealPreflightReport, stable_sha256
from agentskill_eval_real_evidence.spec import ExecutableSpec, RealAgentEvidenceSpec


class RealPreflightError(RuntimeError):
    """Raised before any provider request when frozen prerequisites do not match."""


class RealEvidencePreflight:
    def check(self, spec: RealAgentEvidenceSpec) -> Tuple[RealPreflightReport, LoadedDataset]:
        dataset = DatasetLoader().load(spec.dataset_path)
        selected = self._selected_cases(dataset, spec.case_ids)
        self._validate_real_cases(dataset, selected)
        skill_sha = self._skill_tree_sha256(spec.skill_path, selected)
        baseline_skill_sha = (
            self._skill_tree_sha256(spec.baseline_skill_path, selected)
            if spec.baseline_skill_path is not None
            else None
        )
        version = dataset.dataset_version
        if version is None:
            raise RealPreflightError(
                "real Agent execution requires an immutable DatasetVersion"
            )
        dataset_identity = version.id
        runner = self._probe("runner", spec.runner)
        agent = self._probe("agent", spec.agent)
        self._require_secret_environment(spec.agent.secret_env_names)
        config_sha = stable_sha256(spec.model_dump(mode="json"))
        per_run = spec.pricing.estimated_cost_per_run_microusd
        report = RealPreflightReport(
            config_sha256=config_sha,
            dataset_version_id=dataset_identity,
            dataset_name=dataset.manifest.name,
            dataset_version=dataset.manifest.version,
            evaluation_split=version.split,
            dataset_sha256=dataset.dataset_sha256,
            case_ids=spec.case_ids,
            skill_sha256=skill_sha,
            baseline_skill_sha256=baseline_skill_sha,
            runner=runner,
            agent=agent,
            agent_engine=spec.agent.engine,
            agent_engine_version=spec.agent.engine_version,
            provider=spec.agent.provider,
            model=spec.agent.model,
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            smoke_runs=len(selected) * 2,
            evidence_runs=len(selected) * 2 * spec.protocol.evidence_repeats,
            estimated_input_tokens_per_run=spec.pricing.estimated_input_tokens_per_run,
            estimated_output_tokens_per_run=spec.pricing.estimated_output_tokens_per_run,
            estimated_cost_per_run_microusd=per_run,
            secret_env_names=spec.agent.secret_env_names,
            checked_at=datetime.now(timezone.utc),
        )
        return report, dataset

    @staticmethod
    def _selected_cases(
        dataset: LoadedDataset, case_ids: Tuple[str, ...]
    ) -> Tuple[LoadedCase, ...]:
        by_id = {item.metadata.case_id: item for item in dataset.cases}
        missing = [case_id for case_id in case_ids if case_id not in by_id]
        if missing:
            raise RealPreflightError(f"dataset is missing configured cases: {', '.join(missing)}")
        return tuple(by_id[case_id] for case_id in case_ids)

    @staticmethod
    def _validate_real_cases(dataset: LoadedDataset, cases: Tuple[LoadedCase, ...]) -> None:
        if dataset.manifest.demo_only:
            raise RealPreflightError("real Agent evidence refuses demo_only datasets")
        for case in cases:
            metadata = case.metadata
            if metadata.provenance.source_type != "git_history":
                raise RealPreflightError(f"case {metadata.case_id} is not from Git history")
            if metadata.provenance.synthetic:
                raise RealPreflightError(f"case {metadata.case_id} is synthetic")
            if not metadata.provenance.license:
                raise RealPreflightError(f"case {metadata.case_id} has no license provenance")
            if not {"real-oss", "offline"}.issubset(metadata.tags):
                raise RealPreflightError(
                    f"case {metadata.case_id} lacks real-oss/offline publication tags"
                )
            if case.fixture_path is None or not case.fixture_path.is_dir():
                raise RealPreflightError(f"case {metadata.case_id} has no frozen fixture")

    @staticmethod
    def _skill_tree_sha256(skill_root: Path, cases: Tuple[LoadedCase, ...]) -> str:
        root = skill_root.resolve(strict=True)
        if root.is_symlink() or not root.is_dir():
            raise RealPreflightError("Skill root must be a regular directory")
        skill_file = root / "SKILL.md"
        metadata_file = root / "metadata.yaml"
        if not skill_file.is_file() or skill_file.is_symlink() or not metadata_file.is_file():
            raise RealPreflightError("Skill requires regular SKILL.md and metadata.yaml")
        import yaml

        metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
        digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
        if not isinstance(metadata, dict) or metadata.get("skill_md_sha256") != digest:
            raise RealPreflightError("Skill metadata does not match SKILL.md hash")
        lowered = skill_file.read_text(encoding="utf-8").lower()
        forbidden = {
            value.lower()
            for case in cases
            for value in (case.metadata.case_id, case.metadata.provenance.source_revision)
        }
        if any(value in lowered for value in forbidden):
            raise RealPreflightError("Skill leaks a case ID or source revision")
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RealPreflightError(f"symlink is forbidden in Skill: {path}")
            if path.is_file():
                entries.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        return stable_sha256(entries)

    @staticmethod
    def _probe(name: str, spec: ExecutableSpec) -> ExecutableSnapshot:
        path = spec.path.resolve(strict=True)
        if not path.is_file() or path.is_symlink():
            raise RealPreflightError(f"{name} executable must be a regular file")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != spec.expected_sha256:
            raise RealPreflightError(
                f"{name} executable hash mismatch: expected {spec.expected_sha256}, got {digest}"
            )
        with tempfile.TemporaryDirectory(prefix=f"ase-{name}-preflight-") as home:
            environment = {
                "HOME": home,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": f"{path.parent}{os.pathsep}/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "TZ": "UTC",
            }
            try:
                completed = subprocess.run(
                    (str(path), *spec.version_args),
                    capture_output=True,
                    check=False,
                    env=environment,
                    timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RealPreflightError(f"cannot probe {name} version: {exc}") from exc
        output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        if completed.returncode != 0 or spec.expected_version not in output:
            raise RealPreflightError(
                f"{name} version mismatch: expected output containing {spec.expected_version!r}"
            )
        return ExecutableSnapshot(
            name=name,
            version=spec.expected_version,
            path=str(path),
            sha256=digest,
        )

    @staticmethod
    def _require_secret_environment(names: Tuple[str, ...]) -> None:
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise RealPreflightError(
                "required provider Secret environment variables are missing: " + ", ".join(missing)
            )

    @staticmethod
    def secret_values(names: Tuple[str, ...]) -> Dict[str, str]:
        RealEvidencePreflight._require_secret_environment(names)
        return {name: os.environ[name] for name in names}
