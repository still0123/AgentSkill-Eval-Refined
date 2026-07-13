from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agentskill_eval_benchmark_gen import (
    AutomaticBenchmarkGenerator,
    BenchmarkGenerationError,
    BenchmarkGenerationSpec,
    DatasetLoader,
)
from agentskill_eval_contracts import BenchmarkCandidateStatus, ReviewDecision


def _clone_bundle(bundle: Path, destination: Path, origin: str) -> None:
    subprocess.run(("git", "clone", "--quiet", str(bundle), str(destination)), check=True)
    subprocess.run(
        ("git", "-C", str(destination), "remote", "set-url", "origin", origin),
        check=True,
    )


def test_real_oss_history_can_be_generated_reviewed_and_published(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    bundle = project_root / "examples/benchmark-sources/more-itertools.bundle"
    source = tmp_path / "source"
    subprocess.run(("git", "clone", "--quiet", str(bundle), str(source)), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(source),
            "remote",
            "set-url",
            "origin",
            "https://github.com/more-itertools/more-itertools.git",
        ),
        check=True,
    )
    template = BenchmarkGenerationSpec.load(
        project_root / "examples/benchmark-sources/more-itertools-generation.example.yaml"
    )
    spec = template.model_copy(update={"repository_path": source})
    workspace = tmp_path / "workspace"
    generator = AutomaticBenchmarkGenerator(workspace)

    generated = generator.generate(spec)
    replayed = generator.generate(spec)

    assert replayed.job.id == generated.job.id
    assert replayed.candidates == generated.candidates
    assert [item.status for item in generated.candidates] == [
        BenchmarkCandidateStatus.DEDUPED,
        BenchmarkCandidateStatus.DEDUPED,
    ]
    assert all(len(item.command_evidence) == 12 for item in generated.candidates)
    assert all(all(gate.passed for gate in item.quality_gates) for item in generated.candidates)
    for candidate in generated.candidates:
        history = (
            generator.store.job_dir(generated.job.id)
            / "candidates"
            / str(candidate.id)
            / "history"
        )
        assert [path.name for path in sorted(history.glob("*.json"))] == [
            "0001.json",
            "0002.json",
            "0003.json",
            "0004.json",
        ]
    tamper_target = (
        generator.store.job_dir(generated.job.id)
        / "candidates"
        / str(generated.candidates[0].id)
        / "fixtures"
        / "before"
        / "more_itertools"
        / "more.py"
    )
    original = tamper_target.read_bytes()
    tamper_target.write_bytes(original + b"\n# tampered\n")
    with pytest.raises(BenchmarkGenerationError, match="integrity mismatch"):
        generator.review(
            generated.job.id,
            generated.candidates[0].id,
            "integration-test",
            ReviewDecision.APPROVED,
            "must reject tampered fixture",
        )
    tamper_target.write_bytes(original)
    for candidate in generated.candidates:
        generator.review(
            generated.job.id,
            candidate.id,
            "integration-test",
            ReviewDecision.APPROVED,
            "all evidence manually reviewed",
        )

    version, destination = generator.publish(generated.job.id, "integration-test")
    loaded = DatasetLoader().load(destination)

    assert len(version.cases) == 2
    assert len(loaded.cases) == 2
    assert {item.metadata.case_id for item in loaded.cases} == {
        "more-itertools-last-reversed-none",
        "more-itertools-sample-strict-counts",
    }
    for loaded_case in loaded.cases:
        grader = destination / f"evals/fixtures/scripts/{loaded_case.metadata.case_id}.py"
        grader_text = grader.read_text(encoding="utf-8")
        assert "${PYTHON}" in grader_text
        assert sys.executable not in grader_text
        assert loaded_case.fixture_path is not None
        before_result = subprocess.run(
            (sys.executable, str(grader)),
            cwd=loaded_case.fixture_path,
            check=False,
            capture_output=True,
        )
        assert before_result.returncode != 0
        candidate = next(
            item for item in generated.candidates if item.key == loaded_case.metadata.case_id
        )
        after_fixture = (
            generator.store.job_dir(generated.job.id)
            / "candidates"
            / str(candidate.id)
            / "fixtures"
            / "after"
        )
        after_result = subprocess.run(
            (sys.executable, str(grader)),
            cwd=after_fixture,
            check=False,
            capture_output=True,
        )
        assert after_result.returncode == 0
    assert all(
        generator.store.load_candidate(generated.job.id, candidate.id).status
        == BenchmarkCandidateStatus.PUBLISHED
        for candidate in generated.candidates
    )
    with pytest.raises(BenchmarkGenerationError, match="immutable"):
        generator.store.save_dataset_version(version, destination)


def test_cross_repository_generation_publishes_independent_families_and_blocks_split_leakage(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    source_root = tmp_path / "sources"
    source_root.mkdir()
    more_itertools = source_root / "more-itertools"
    cachetools = source_root / "cachetools"
    _clone_bundle(
        project_root / "examples/benchmark-sources/more-itertools.bundle",
        more_itertools,
        "https://github.com/more-itertools/more-itertools.git",
    )
    _clone_bundle(
        project_root / "examples/benchmark-sources/cachetools.bundle",
        cachetools,
        "https://github.com/tkem/cachetools.git",
    )
    template = BenchmarkGenerationSpec.load(
        project_root
        / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    paths = {"more-itertools": more_itertools, "cachetools": cachetools}
    sources = tuple(
        source.model_copy(update={"repository_path": paths[source.key]})
        for source in template.sources
    )
    spec = template.model_copy(update={"sources": sources})
    workspace = tmp_path / "workspace"
    generator = AutomaticBenchmarkGenerator(workspace)

    generated = generator.generate(spec)

    assert len(generated.candidates) == 4
    assert all(
        candidate.status == BenchmarkCandidateStatus.DEDUPED
        and len(candidate.command_evidence) == 12
        for candidate in generated.candidates
    )
    assert {
        candidate.provenance.repository_url  # type: ignore[union-attr]
        for candidate in generated.candidates
    } == {
        "https://github.com/more-itertools/more-itertools.git",
        "https://github.com/tkem/cachetools.git",
    }
    for candidate in generated.candidates:
        generator.review(
            generated.job.id,
            candidate.id,
            "integration-test",
            ReviewDecision.APPROVED,
            "cross-repository evidence reviewed",
        )
    version, destination = generator.publish(generated.job.id, "integration-test")
    loaded = DatasetLoader().load(destination)

    assert len(version.source_lineages) == 2
    assert len(loaded.independence_groups) == 4
    assert all(case.metadata_sha256 is not None for case in version.cases)
    generator.store.assert_dataset_version_integrity(version, destination)

    confirm_spec = spec.model_copy(
        update={
            "name": "cross-repository-split-leakage-check",
            "version": "2026.07.14-confirm",
            "target_split": "validation_confirm",
            "candidates": (spec.candidates[0],),
            "budget": spec.budget.model_copy(
                update={"max_candidates": 1, "max_commands": 12}
            ),
        }
    )
    confirm = generator.generate(confirm_spec)
    generator.review(
        confirm.job.id,
        confirm.candidates[0].id,
        "integration-test",
        ReviewDecision.APPROVED,
        "candidate evidence reviewed before split leakage gate",
    )
    with pytest.raises(BenchmarkGenerationError, match="crosses published dataset splits"):
        generator.publish(confirm.job.id, "integration-test")

    metadata = destination / f"metadata/{version.cases[0].case_id}.yaml"
    metadata.write_text(metadata.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
    with pytest.raises(BenchmarkGenerationError, match="metadata integrity mismatch"):
        generator.store.published_dataset_versions()
