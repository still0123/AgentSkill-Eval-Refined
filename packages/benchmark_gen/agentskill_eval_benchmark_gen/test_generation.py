"""Build two-case regression-test generation datasets from frozen Git history."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence, Tuple, cast
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentskill_eval_benchmark_gen.dataset import DatasetLoader
from agentskill_eval_benchmark_gen.git_source import GitSource, GitTreeLimits
from agentskill_eval_benchmark_gen.spec import BenchmarkGenerationSpec, CandidateSpec
from agentskill_eval_contracts import (
    BenchmarkDatasetVersion,
    PublishedCase,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment.storage.manifests import model_bytes


class TestGenerationError(RuntimeError):
    """Raised when a test-generation fixture cannot be frozen safely."""


class TestGenerationBuildResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_root: Path
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_ids: Tuple[str, str]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class TestGenerationDatasetBuilder:
    """Reuse verified before/after commits while changing only the Agent task and grader."""

    TEST_PATH = "agent_regression_test.py"

    def build(
        self,
        catalog: BenchmarkGenerationSpec,
        *,
        case_keys: Sequence[str],
        repository_roots: Mapping[str, Path],
        output: Path,
    ) -> TestGenerationBuildResult:
        if len(case_keys) != 2 or len(set(case_keys)) != 2:
            raise TestGenerationError("test-generation evaluation requires exactly two Cases")
        by_key = {item.key: item for item in catalog.candidates}
        try:
            selected = tuple(by_key[key] for key in case_keys)
        except KeyError as exc:
            raise TestGenerationError(f"unknown benchmark candidate: {exc.args[0]}") from exc
        output = output.resolve()
        if output.exists():
            raise TestGenerationError("test-generation dataset output already exists")
        output.mkdir(parents=True)
        source_by_key = {item.key: item for item in catalog.sources}
        metadata_paths = []
        manifest_cases = []
        published_cases = []
        source_lineages = []
        for item in selected:
            source_spec = source_by_key[item.source_key]
            source_lineages.append(source_spec.fork_lineage)
            repository = repository_roots.get(item.source_key)
            if repository is None:
                raise TestGenerationError(
                    f"missing offline repository for source {item.source_key}"
                )
            source = GitSource(repository)
            before = source.resolve_commit(item.before_commit)
            after = source.resolve_commit(item.after_commit)
            source.assert_ancestor(before, after)
            case_id = f"testgen-{item.key}"
            fixture_ref = f"evals/fixtures/repos/{case_id}"
            fixture = output / fixture_ref
            source.materialize(
                before,
                fixture,
                GitTreeLimits(
                    catalog.quality_gate.max_repository_files,
                    catalog.quality_gate.max_repository_bytes,
                ),
            )
            before_hashes = {
                path: _sha((fixture / path).read_bytes()) for path in item.production_paths
            }
            after_files = {
                path: base64.b64encode(source.blob(after, path)).decode("ascii")
                for path in item.production_paths
            }
            grader_ref = f"evals/fixtures/scripts/{case_id}.py"
            grader_path = output / grader_ref
            grader_path.parent.mkdir(parents=True, exist_ok=True)
            grader_path.write_text(
                self._grader(before_hashes, after_files),
                encoding="utf-8",
            )
            reference_validation_sha256 = self._validate_reference_oracle(
                source,
                after,
                item,
                fixture,
                grader_path,
            )
            case_ref = f"evals/cases/{case_id}.yaml"
            case_path = output / case_ref
            case_path.parent.mkdir(parents=True, exist_ok=True)
            case_payload = {
                "id": case_id,
                "input": {"prompt": self._prompt(item)},
                "context": {"repo_fixture": fixture_ref},
                "expect": {"exit_code": 0},
                "judge": {
                    "type": "script",
                    "script_path": grader_ref,
                    "timeout_seconds": 90,
                },
            }
            case_path.write_text(
                yaml.safe_dump(case_payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            metadata_ref = f"metadata/{case_id}.yaml"
            metadata_path = output / metadata_ref
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            family = item.provenance_family or after
            metadata = {
                "schema_version": "ase-case-meta/v1alpha1",
                "case_id": case_id,
                "case_ref": case_ref,
                "split": "validation_search",
                "category": "positive",
                "skill_applicable": True,
                "group_keys": {
                    "independence_group": f"{source_spec.fork_lineage}#testgen-{family}",
                    "repository": source_spec.repository_url,
                    "fork_lineage": source_spec.fork_lineage,
                    "patch_family": f"testgen-{family}",
                },
                "provenance": {
                    "source_type": "git_history",
                    "source_revision": after,
                    "license": source_spec.license_spdx,
                    "contamination_risk": source_spec.contamination_risk,
                    "synthetic": False,
                },
                "oracle": {
                    "kind": "script",
                    "expected_signal": "generated test fails before and passes after",
                },
                "tags": ["generated", "real-oss", "offline", "python", "test-generation"],
            }
            metadata_path.write_text(
                yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            provenance_ref = f"provenance/{case_id}.json"
            provenance_path = output / provenance_ref
            provenance_path.parent.mkdir(parents=True, exist_ok=True)
            provenance = {
                "case_id": case_id,
                "source_candidate": item.key,
                "repository_url": source_spec.repository_url,
                "before_commit": before,
                "after_commit": after,
                "production_paths": list(item.production_paths),
                "generated_test_path": self.TEST_PATH,
                "reference_patch_exposed": False,
                "contamination_risk": source_spec.contamination_risk,
            }
            provenance_path.write_bytes(canonical_json(provenance) + b"\n")
            metadata_paths.append(metadata_ref)
            manifest_cases.append(
                {
                    "case_id": case_id,
                    "case_sha256": _sha(case_path.read_bytes()),
                    "fixture_sha256": self._tree_sha(fixture),
                    "grader_sha256": _sha(grader_path.read_bytes()),
                    "metadata_sha256": _sha(metadata_path.read_bytes()),
                    "provenance_sha256": _sha(provenance_path.read_bytes()),
                    "reference_validation_sha256": reference_validation_sha256,
                }
            )
            published_cases.append(
                PublishedCase(
                    candidate_id=uuid5(
                        NAMESPACE_URL,
                        f"agentskill-eval:test-generation-candidate:{item.key}:{before}:{after}",
                    ),
                    case_id=case_id,
                    case_sha256=_sha(case_path.read_bytes()),
                    fixture_sha256=self._tree_sha(fixture),
                    grader_sha256=_sha(grader_path.read_bytes()),
                    provenance_sha256=_sha(provenance_path.read_bytes()),
                    metadata_sha256=_sha(metadata_path.read_bytes()),
                )
            )
        dataset_name = "python-test-generation-real-paired"
        dataset_version = "2026.08.05.1"
        dataset = {
            "schema_version": "ase-dataset/v1alpha1",
            "name": dataset_name,
            "version": dataset_version,
            "description": (
                "Two public Git-history Cases for writing a regression test without changing "
                "production code."
            ),
            "domain": "software-engineering-test-generation",
            "license": "Mixed permissive OSS; see per-Case metadata",
            "runner_name": "skill-up",
            "runner_version": "0.5.0",
            "demo_only": False,
            "expected_case_count": 2,
            "case_metadata": metadata_paths,
            "minimum_category_counts": {"positive": 2},
        }
        (output / "dataset.yaml").write_text(
            yaml.safe_dump(dataset, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        frozen_cases = tuple(published_cases)
        content_sha256 = BenchmarkDatasetVersion.calculate_content_sha256(frozen_cases)
        job_id = uuid5(
            NAMESPACE_URL,
            "agentskill-eval:test-generation-job:"
            + stable_sha256(
                {
                    "catalog": catalog.name,
                    "catalog_version": catalog.version,
                    "case_ids": list(case_keys),
                }
            ),
        )
        version = BenchmarkDatasetVersion(
            id=uuid5(
                NAMESPACE_URL,
                f"agentskill-eval:test-generation-dataset:{content_sha256}",
            ),
            name=dataset_name,
            version=dataset_version,
            split="validation_search",
            job_id=job_id,
            published_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            publisher="test-generation-dataset-builder",
            cases=frozen_cases,
            content_sha256=content_sha256,
            source_lineages=tuple(sorted(set(source_lineages))),
            metadata={
                "evidence_class": "public-git-history-descriptive",
                "task_family": "python-test-generation",
            },
        )
        (output / "dataset-version.json").write_bytes(model_bytes(version))
        release = {
            "schema_version": "ase/test-generation-dataset/v1alpha1",
            "dataset_version_id": str(version.id),
            "dataset_version_content_sha256": version.content_sha256,
            "source_catalog": catalog.name,
            "source_catalog_version": catalog.version,
            "cases": manifest_cases,
            "reference_patch_exposed": False,
        }
        release["content_sha256"] = stable_sha256(release)
        release_path = output / "test-generation-dataset.json"
        release_path.write_bytes(canonical_json(release) + b"\n")
        loaded = DatasetLoader().load(output)
        case_ids = cast(
            Tuple[str, str],
            tuple(item.metadata.case_id for item in loaded.cases),
        )
        return TestGenerationBuildResult(
            dataset_root=output,
            dataset_sha256=loaded.dataset_sha256,
            case_ids=case_ids,
            manifest_sha256=_sha(release_path.read_bytes()),
        )

    def _prompt(self, item: CandidateSpec) -> str:
        return (
            f"Write {self.TEST_PATH} as a standalone Python regression test that exposes this "
            f"defect: {item.task} Do not modify production code. The test must exit non-zero on "
            "the current buggy checkout and exit zero after the production defect is fixed. "
            "Use deterministic offline assertions and run the test before finishing."
        )

    @classmethod
    def _validate_reference_oracle(
        cls,
        source: GitSource,
        after_commit: str,
        item: CandidateSpec,
        fixture: Path,
        grader: Path,
    ) -> str:
        evidence = []
        for repeat in range(1, 4):
            with tempfile.TemporaryDirectory(prefix="ase-testgen-reference-") as temporary:
                root = Path(temporary) / "repo"
                shutil.copytree(fixture, root)
                for relative in item.regression_test_paths:
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.blob(after_commit, relative))
                argv = [
                    sys.executable if token == "${PYTHON}" else token
                    for token in item.test_command
                ]
                (root / cls.TEST_PATH).write_text(
                    "import subprocess\n"
                    "import sys\n"
                    f"raise SystemExit(subprocess.call({argv!r}))\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    (sys.executable, str(grader)),
                    cwd=root,
                    capture_output=True,
                    check=False,
                    timeout=90,
                )
                evidence.append(
                    {
                        "repeat": repeat,
                        "exit_code": result.returncode,
                        "stdout_sha256": _sha(result.stdout),
                        "stderr_sha256": _sha(result.stderr),
                    }
                )
                if result.returncode != 0:
                    raise TestGenerationError(
                        f"reference test-generation oracle failed for {item.key}"
                    )
        return stable_sha256(evidence)

    @classmethod
    def _grader(
        cls,
        before_hashes: Mapping[str, str],
        after_files: Mapping[str, str],
    ) -> str:
        before_json = json.dumps(dict(before_hashes), sort_keys=True)
        after_json = json.dumps(dict(after_files), sort_keys=True)
        return f"""#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.cwd()
TEST = ROOT / {cls.TEST_PATH!r}
BEFORE = {before_json}
AFTER = {after_json}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_test(root: Path) -> int:
    guard = root / ".agentskill-eval-test-guard"
    guard.mkdir(exist_ok=True)
    (guard / "sitecustomize.py").write_text(
        "import socket\\n"
        "def deny(*args, **kwargs): raise RuntimeError('network disabled')\\n"
        "socket.create_connection = deny\\n"
        "socket.socket.connect = deny\\n",
        encoding="utf-8",
    )
    paths = [str(guard), str(root)]
    if (root / "src").is_dir():
        paths.append(str(root / "src"))
    env = {{
        "HOME": str(root / ".agentskill-eval-test-home"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": os.pathsep.join(paths),
        "TZ": "UTC",
    }}
    return subprocess.run(
        [sys.executable, {cls.TEST_PATH!r}],
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
        timeout=30,
    ).returncode


def main() -> int:
    if not TEST.is_file() or TEST.is_symlink() or TEST.stat().st_size > 50000:
        return 2
    if any(sha(ROOT / path) != expected for path, expected in BEFORE.items()):
        return 3
    before_exit = run_test(ROOT)
    if any(sha(ROOT / path) != expected for path, expected in BEFORE.items()):
        return 4
    with tempfile.TemporaryDirectory(prefix="ase-testgen-") as temporary:
        after_root = Path(temporary) / "repo"
        shutil.copytree(
            ROOT,
            after_root,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".agentskill-eval-test-*"
            ),
        )
        for relative, encoded in AFTER.items():
            target = after_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded))
        after_exit = run_test(after_root)
    if before_exit == 0 or after_exit != 0:
        return 5
    print("PASS: generated regression test fails before and passes after")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

    @staticmethod
    def _tree_sha(root: Path) -> str:
        entries = [
            (path.relative_to(root).as_posix(), _sha(path.read_bytes()))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        return stable_sha256(entries)
