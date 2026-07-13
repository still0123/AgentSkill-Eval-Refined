from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentskill_eval_contracts import (
    CandidateOrigin,
    OptimizationJobStatus,
    SkillCandidateStatus,
)
from agentskill_eval_skill_optimizer import (
    BenchmarkGuidedSkillSearch,
    EvaluatorSpec,
    OptimizationSearchSpec,
    SkillSearchError,
)


def test_successive_halving_freezes_search_winner_without_locked_test(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    spec = OptimizationSearchSpec.load(
        project_root / "examples/optimizer/python-review-search/search.example.yaml"
    )
    search = BenchmarkGuidedSkillSearch(tmp_path / "workspace")

    result = search.run(spec)
    replayed = search.run(spec)

    assert result.job.status == OptimizationJobStatus.FROZEN
    assert result.job.locked_test_accessed is False
    assert result.job.simulated is True
    assert result.job.evaluations_used == 76
    assert result.winner.origin == CandidateOrigin.SEARCH
    assert result.winner.status == SkillCandidateStatus.FROZEN
    assert len(result.candidates) == 7
    assert len([item for item in result.candidates if item.origin == CandidateOrigin.SEARCH]) == 4
    assert {item.origin for item in result.candidates} == set(CandidateOrigin)
    assert replayed.job == result.job
    assert replayed.candidates == result.candidates
    report = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert report["simulated"] is True
    assert report["locked_test_accessed"] is False
    assert report["group_coverage"] == ["manual", "original", "random", "search"]
    assert "not Agent performance evidence" in report["claim_limit"]
    assert any(
        row["origin"] == "search" and row["full_pass_rate"] is None
        for row in report["candidates"]
    )
    assert all(
        sorted(
            (
                search.store.job_dir(result.job.id)
                / "candidates"
                / str(candidate.id)
                / "history"
            ).glob("*.json")
        )
        for candidate in result.candidates
    )

    winner_path = search.store.skill_path(result.winner)
    original = winner_path.read_bytes()
    winner_path.write_bytes(original + b"\n# tampered\n")
    with pytest.raises(SkillSearchError, match="integrity mismatch"):
        search.run(spec)


def test_search_spec_has_no_locked_test_input() -> None:
    assert "locked_test" not in OptimizationSearchSpec.model_fields


def test_process_search_refuses_dataset_with_non_search_splits(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    template = OptimizationSearchSpec.load(
        project_root / "examples/optimizer/python-review-search/search.example.yaml"
    )
    spec = template.model_copy(
        update={
            "validation_search_path": project_root / "examples/datasets/python-review-demo",
            "evaluator": EvaluatorSpec(
                type="process",
                command=(sys.executable, "unused.py"),
                version="test-v1",
                simulated=True,
            ),
        }
    )

    with pytest.raises(SkillSearchError, match="validation_search cases only"):
        BenchmarkGuidedSkillSearch(tmp_path).run(spec)
