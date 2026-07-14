from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentskill_eval_benchmark_gen import (
    BenchmarkGenerationSpec,
    BenchmarkSplitPlan,
    BenchmarkSplitPlanError,
    DatasetSplit,
    ExposureZone,
    exposure_zone,
)

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_single_source_spec_is_preserved() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/more-itertools-generation.example.yaml"
    )

    sources = spec.repository_sources()

    assert spec.schema_version == "ase/benchmark-generation/v1alpha1"
    assert len(sources) == 1
    assert sources[0].key == "primary"
    assert all(candidate.source_key == "primary" for candidate in spec.candidates)


def test_multi_source_semantic_payload_excludes_machine_paths() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    relocated = spec.model_copy(
        update={
            "sources": tuple(
                source.model_copy(
                    update={"repository_path": Path("/different-machine") / source.key}
                )
                for source in spec.sources
            )
        }
    )

    assert spec.semantic_payload() == relocated.semantic_payload()
    assert len(spec.repository_sources()) == 2


def test_multi_source_spec_rejects_unknown_candidate_source() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    payload = spec.model_dump(mode="json")
    payload["candidates"][0]["source_key"] = "missing-source"

    with pytest.raises(ValidationError, match="unknown candidate source_key"):
        BenchmarkGenerationSpec.model_validate(payload)


def test_real_bug_fix_split_plan_covers_expanded_candidates_once() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    plan = BenchmarkSplitPlan.load(
        ROOT / "examples/benchmark-sources/real-bug-fix-split-plan.yaml"
    )
    assigned = [
        case_id for case_ids in plan.splits.by_split().values() for case_id in case_ids
    ]

    assert len(spec.candidates) == 12
    assert len(assigned) == len(set(assigned)) == 12
    assert set(assigned) == {item.key for item in spec.candidates}
    assert len(plan.splits.train) == 1
    assert len(plan.splits.validation_search) == 2
    assert len(plan.splits.regression_dev) == 1
    assert len(plan.splits.validation_confirm) == 4
    assert len(plan.splits.locked_test) == 4
    assert plan.audit().passed
    assert exposure_zone(DatasetSplit.TRAIN) == ExposureZone.ADAPTIVE
    assert exposure_zone(DatasetSplit.LOCKED_TEST) == ExposureZone.HOLDOUT


def test_split_plan_rejects_repository_crossing_exposure_boundary(tmp_path: Path) -> None:
    source = ROOT / "examples/benchmark-sources/real-bug-fix-split-plan.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["source_spec"] = str(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    leaked = payload["splits"]["validation_confirm"].pop(0)
    payload["splits"]["train"].append(leaked)
    path = tmp_path / "leaked-plan.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(BenchmarkSplitPlanError, match="exposure boundary"):
        BenchmarkSplitPlan.load(path)


def test_split_plan_requires_nonempty_locked_test(tmp_path: Path) -> None:
    source = ROOT / "examples/benchmark-sources/real-bug-fix-split-plan.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["source_spec"] = str(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    payload["splits"]["validation_confirm"].extend(payload["splits"]["locked_test"])
    payload["splits"]["locked_test"] = []
    path = tmp_path / "empty-locked.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(BenchmarkSplitPlanError, match="locked_test"):
        BenchmarkSplitPlan.load(path)


def test_split_plan_derives_one_generation_spec_per_split() -> None:
    plan = BenchmarkSplitPlan.load(
        ROOT / "examples/benchmark-sources/real-bug-fix-split-plan.yaml"
    )

    locked = plan.generation_spec(DatasetSplit.LOCKED_TEST)

    assert locked.target_split == "locked_test"
    assert len(locked.candidates) == 4
    assert {item.source_key for item in locked.candidates} == {"cachetools"}
    assert {item.key for item in locked.candidates} == set(plan.splits.locked_test)
    assert {item.key for item in locked.sources} == {"cachetools"}
