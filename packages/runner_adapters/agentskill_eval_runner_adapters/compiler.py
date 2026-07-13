"""Compile one platform run into an isolated skill-up evaluation directory."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from agentskill_eval_runner_adapters.contracts import RunnerRequest


class CompilationError(ValueError):
    """Raised when source material cannot be copied safely."""


@dataclass(frozen=True)
class CompiledEvaluation:
    root: Path
    eval_path: Path
    output_dir: Path


def _copy_tree_without_links(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise CompilationError(f"symlink is not allowed: {source}")
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise CompilationError(f"source is not a directory: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target = destination / relative
        for name in tuple(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise CompilationError(f"symlink is not allowed: {candidate}")
            (target / name).mkdir(parents=True, exist_ok=True)
        for name in files:
            candidate = current_path / name
            if candidate.is_symlink():
                raise CompilationError(f"symlink is not allowed: {candidate}")
            shutil.copy2(candidate, target / name)


def _case_path(request: RunnerRequest) -> str:
    source = request.source_eval_dir.resolve(strict=True)
    case = request.case_file.resolve(strict=True)
    try:
        relative = case.relative_to(source)
    except ValueError as exc:
        raise CompilationError("case_file must be inside source_eval_dir") from exc
    if case.is_symlink() or not case.is_file():
        raise CompilationError("case_file must be a regular file")
    return (Path("evals") / relative).as_posix()


def compile_evaluation(request: RunnerRequest) -> CompiledEvaluation:
    """Build a clean, single-case, single-variant skill-up input tree."""
    root = request.run_dir / "compiled"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    eval_dir = root / "evals"
    _copy_tree_without_links(request.source_eval_dir, eval_dir)

    # Anchors skill-up's upward root lookup inside the run and prevents accidental
    # discovery of a developer's surrounding SKILL.md.
    (root / "SKILL.md").write_text(
        "---\nname: agentskill-eval-neutral-root\n---\n# Evaluation root\n",
        encoding="utf-8",
    )

    skills = []
    if request.skill_path is not None:
        skill_destination = root / "skills" / "selected"
        _copy_tree_without_links(request.skill_path, skill_destination)
        if not (skill_destination / "SKILL.md").is_file():
            raise CompilationError("treatment skill must contain SKILL.md")
        skills = [{"path": "skills/selected"}]

    config: Dict[str, Any] = {
        "schema_version": "v1alpha1",
        "environment": dict(request.environment),
        "mcp": dict(request.mcp),
        "skills": skills,
        "engine": dict(request.engine),
        "cases": {
            "files": [_case_path(request)],
            "defaults": {
                "timeout_seconds": request.timeout_seconds,
                "max_turns": request.max_turns,
                "collect_artifacts": list(request.collect_artifacts),
            },
            "parallelism": 1,
            "retry_policy": {"max_retries": 0, "retry_on": []},
        },
        "benchmark": {"enabled": False},
        "report": {"formats": ["json"], "artifacts": ["transcript"]},
    }
    eval_path = eval_dir / "eval.yaml"
    # JSON is valid YAML 1.2 and avoids adding a second configuration parser.
    eval_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_dir = request.run_dir / "runner-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return CompiledEvaluation(root=root, eval_path=eval_path, output_dir=output_dir)
