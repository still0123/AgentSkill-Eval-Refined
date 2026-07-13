from __future__ import annotations

import shutil
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from agentskill_eval_benchmark_gen import CaseCategory, DatasetLoader

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples/datasets/python-review-demo"


def test_demo_dataset_is_frozen_importable_and_balanced() -> None:
    dataset = DatasetLoader().load(DEMO)

    assert dataset.manifest.demo_only is True
    assert len(dataset.cases) == 12
    assert len(dataset.execution_specs()) == 12
    assert len(dataset.independence_groups) == 6
    assert dataset.category_counts == {
        CaseCategory.POSITIVE: 4,
        CaseCategory.NEGATIVE: 2,
        CaseCategory.DISTRACTOR: 2,
        CaseCategory.COMPLEX: 2,
        CaseCategory.ROBUSTNESS: 2,
    }
    assert len({case.case_sha256 for case in dataset.cases}) == 12
    assert all(case.fixture_path is not None for case in dataset.cases)


def test_dataset_identity_and_execution_specs_are_deterministic() -> None:
    first = DatasetLoader().load(DEMO)
    second = DatasetLoader().load(DEMO)

    assert first.dataset_id == second.dataset_id
    assert first.dataset_sha256 == second.dataset_sha256
    assert first.execution_specs() == second.execution_specs()


def test_demo_skill_metadata_freezes_skill_content() -> None:
    skill_dir = ROOT / "examples/skills/python-review-v1"
    metadata = yaml.safe_load((skill_dir / "metadata.yaml").read_text(encoding="utf-8"))

    assert metadata["version"] == "1.0.0"
    actual_hash = sha256((skill_dir / "SKILL.md").read_bytes()).hexdigest()
    assert metadata["skill_md_sha256"] == actual_hash


def test_fixture_change_changes_case_and_dataset_identity(tmp_path: Path) -> None:
    copied = tmp_path / "dataset"
    shutil.copytree(DEMO, copied)
    before = DatasetLoader().load(copied)
    fixture = copied / "evals/fixtures/repos/python-env-bool/config.py"
    fixture.write_text(fixture.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    after = DatasetLoader().load(copied)

    before_case = next(item for item in before.cases if item.metadata.case_id == "python-env-bool")
    after_case = next(item for item in after.cases if item.metadata.case_id == "python-env-bool")
    assert before_case.case_sha256 != after_case.case_sha256
    assert before.dataset_sha256 != after.dataset_sha256
    assert before.dataset_id != after.dataset_id


def test_loader_rejects_path_escape(tmp_path: Path) -> None:
    copied = tmp_path / "dataset"
    shutil.copytree(DEMO, copied)
    manifest_path = copied / "dataset.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["case_metadata"][0] = "../outside.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe dataset path"):
        DatasetLoader().load(copied)


def test_loader_rejects_case_id_mismatch(tmp_path: Path) -> None:
    copied = tmp_path / "dataset"
    shutil.copytree(DEMO, copied)
    case_path = copied / "evals/cases/python-null-dereference.yaml"
    payload = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    payload["id"] = "different-id"
    case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="case ID mismatch"):
        DatasetLoader().load(copied)
