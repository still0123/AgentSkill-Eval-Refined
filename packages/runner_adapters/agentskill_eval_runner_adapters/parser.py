"""Forward-compatible parser for skill-up's public result.json contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from agentskill_eval_runner_adapters.contracts import (
    ArtifactObservation,
    ExitReason,
    RunnerResult,
    RunnerStatus,
)


class ResultParseError(ValueError):
    """Raised when the public result contract is absent or malformed."""


_TOOL_BUDGET = re.compile(
    r"tool-call budget of (?P<limit>\d+) exceeded .*? observed (?P<observed>\d+)",
    re.IGNORECASE | re.DOTALL,
)
_TURN_LIMIT = re.compile(r"reached max session turns", re.IGNORECASE)
_LOOP_DETECTED = re.compile(r"loop detection halted the run", re.IGNORECASE)


def _optional_int(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _artifacts(root: Path) -> Tuple[ArtifactObservation, ...]:
    observations = []
    if not root.exists():
        return ()
    resolved_root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ResultParseError(f"runner output contains symlink: {path}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ResultParseError(f"artifact escaped runner output: {path}") from exc
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        observations.append(ArtifactObservation(relative.as_posix(), digest, path.stat().st_size))
    return tuple(observations)


def parse_skill_up_result(
    result_path: Path,
    execution_id: str,
    case_id: str,
    process_exit_code: Optional[int],
    stdout: str = "",
    stderr: str = "",
) -> RunnerResult:
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultParseError(f"cannot read result.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ResultParseError("result.json root must be an object")
    cases = raw.get("case_results")
    if not isinstance(cases, list):
        raise ResultParseError("result.json case_results must be an array")
    matches = [item for item in cases if isinstance(item, dict) and item.get("case_id") == case_id]
    if len(matches) != 1:
        raise ResultParseError(f"expected exactly one result for case {case_id!r}")
    case: Mapping[str, Any] = matches[0]
    try:
        status = RunnerStatus(str(case["status"]).upper())
    except (KeyError, ValueError) as exc:
        raise ResultParseError(f"unsupported case status: {case.get('status')!r}") from exc
    error = case.get("error")
    error_text = error if isinstance(error, str) else ""
    budget_match = _TOOL_BUDGET.search(error_text)
    if status == RunnerStatus.PASS:
        reason = ExitReason.COMPLETED
    elif status == RunnerStatus.FAIL:
        reason = ExitReason.CASE_FAILED
    elif budget_match is not None:
        reason = ExitReason.BUDGET_EXHAUSTED
    elif _TURN_LIMIT.search(error_text) is not None:
        reason = ExitReason.TURN_LIMIT
    elif _LOOP_DETECTED.search(error_text) is not None:
        reason = ExitReason.LOOP_DETECTED
    else:
        reason = ExitReason.EXECUTION_ERROR
    grading = case.get("grading")
    response = case.get("response")
    final_message = response if isinstance(response, str) else ""
    return RunnerResult(
        execution_id=execution_id,
        case_id=case_id,
        status=status,
        exit_reason=reason,
        process_exit_code=process_exit_code,
        duration_ms=_optional_int(case.get("duration_ms")),
        turns=_optional_int(case.get("turns")),
        input_tokens=_optional_int(case.get("input_tokens")),
        output_tokens=_optional_int(case.get("output_tokens")),
        cached_input_tokens=_optional_int(case.get("cached_input_tokens")),
        tool_calls=(
            _optional_int(case.get("tool_calls"))
            or (int(budget_match.group("observed")) if budget_match is not None else None)
        ),
        cost_microusd=_optional_int(case.get("cost_microusd")),
        final_message=final_message,
        grading=grading if isinstance(grading, dict) else {},
        artifacts=_artifacts(result_path.parent),
        stdout=stdout,
        stderr=stderr,
        raw_result=raw,
    )
