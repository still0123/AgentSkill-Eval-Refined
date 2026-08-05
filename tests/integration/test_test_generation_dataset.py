from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agentskill_eval_benchmark_gen import (
    BenchmarkGenerationSpec,
    CandidateSpec,
    DatasetLoader,
    RepositorySourceSpec,
    TestGenerationDatasetBuilder,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def test_test_generation_grader_requires_before_fail_after_pass(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "fixture@example.com")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (repository / "module.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "before")
    before = _git(repository, "rev-parse", "HEAD")
    (repository / "module.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    (repository / "test_module.py").write_text(
        "from module import value\n"
        "assert value() == 2\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "after")
    after = _git(repository, "rev-parse", "HEAD")
    catalog = BenchmarkGenerationSpec(
        schema_version="ase/benchmark-generation/v1alpha2",
        name="test-generation-fixture",
        version="1",
        sources=(
            RepositorySourceSpec(
                key="fixture",
                repository_path=repository,
                repository_url="https://example.com/fixture.git",
                fork_lineage="example.com/fixture",
                license_spdx="MIT",
                license_path="LICENSE",
                contamination_risk="low",
            ),
        ),
        candidates=(
            CandidateSpec(
                key="fixture-regression",
                source_key="fixture",
                provenance_family="return-value",
                before_commit=before,
                after_commit=after,
                task="Make value() return the documented value of two.",
                production_paths=("module.py",),
                regression_test_paths=("test_module.py",),
                test_command=("${PYTHON}", "test_module.py"),
                alternative_patch="diff --git a/module.py b/module.py\nplaceholder\n",
                tags=("python", "regression"),
            ),
            CandidateSpec(
                key="fixture-regression-copy",
                source_key="fixture",
                provenance_family="return-value-copy",
                before_commit=before,
                after_commit=after,
                task="Cover the documented value of two with a regression test.",
                production_paths=("module.py",),
                regression_test_paths=("test_module.py",),
                test_command=("${PYTHON}", "test_module.py"),
                alternative_patch="diff --git a/module.py b/module.py\nplaceholder\n",
                tags=("python", "regression"),
            ),
        ),
    )
    output = tmp_path / "dataset"
    result = TestGenerationDatasetBuilder().build(
        catalog,
        case_keys=("fixture-regression", "fixture-regression-copy"),
        repository_roots={"fixture": repository},
        output=output,
    )

    assert len(DatasetLoader().load(result.dataset_root).cases) == 2
    case_id = "testgen-fixture-regression"
    fixture = output / "evals" / "fixtures" / "repos" / case_id
    generated_test = fixture / TestGenerationDatasetBuilder.TEST_PATH
    generated_test.write_text(
        "from module import value\n"
        "assert value() == 2, 'value must follow the documented contract'\n",
        encoding="utf-8",
    )
    grader = output / "evals" / "fixtures" / "scripts" / f"{case_id}.py"
    assert grader.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")

    accepted = subprocess.run(
        (sys.executable, str(grader)),
        cwd=fixture,
        capture_output=True,
        check=False,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    (fixture / "module.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    production_edit = subprocess.run(
        (sys.executable, str(grader)),
        cwd=fixture,
        capture_output=True,
        check=False,
    )
    assert production_edit.returncode == 3
