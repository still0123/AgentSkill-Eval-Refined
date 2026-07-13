"""CLI/JSON anti-corruption adapter for the pinned skill-up release."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional

from agentskill_eval_runner_adapters.compiler import (
    CompilationError,
    compile_evaluation,
    inspect_compiled_skill,
)
from agentskill_eval_runner_adapters.contracts import (
    CapabilityLevel,
    ExitReason,
    RunnerCompatibility,
    RunnerEvent,
    RunnerRequest,
    RunnerResult,
    RunnerStatus,
    TraceEventSink,
    ValidationReport,
    null_event_sink,
)
from agentskill_eval_runner_adapters.parser import ResultParseError, parse_skill_up_result
from agentskill_eval_runner_adapters.process import ProcessSupervisor

SKILL_UP_VERSION = "0.5.0"
SKILL_UP_BINARY_SHA256 = "b8473aad3fe997f3aa8de1e9bd9bc127e5254b25371567a0e07143afc809c359"
_RESERVED_ENV = {"HOME", "PATH", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"}
_TRACE_KIND = re.compile(r"^(tool|file|command|test|runner)\.[a-z0-9_.-]+$")
_SAFE_HOME_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")


class IncompatibleRunnerError(RuntimeError):
    """Raised when the executable is not the pinned, verified runner."""


class SkillUpRunnerAdapter:
    def __init__(
        self,
        binary: Path,
        *,
        expected_sha256: str = SKILL_UP_BINARY_SHA256,
        agent_executable: Optional[Path] = None,
        supervisor: Optional[ProcessSupervisor] = None,
    ) -> None:
        self.binary = binary.resolve(strict=True)
        self.expected_sha256 = expected_sha256
        self.agent_executable = (
            agent_executable.resolve(strict=True) if agent_executable is not None else None
        )
        self._supervisor = supervisor or ProcessSupervisor()

    @property
    def compatibility(self) -> RunnerCompatibility:
        return RunnerCompatibility(
            name="skill-up",
            version=SKILL_UP_VERSION,
            binary_sha256=self.expected_sha256,
            capabilities={
                "single_case": CapabilityLevel.NATIVE,
                "baseline_treatment": CapabilityLevel.EMULATED,
                "cancellation": CapabilityLevel.EMULATED,
                "structured_result": CapabilityLevel.NATIVE,
            },
        )

    def verify_binary(self) -> None:
        digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        if digest != self.expected_sha256:
            raise IncompatibleRunnerError(
                f"skill-up binary hash mismatch: expected {self.expected_sha256}, got {digest}"
            )

    def _environment(self, request: RunnerRequest, home: Path) -> Mapping[str, str]:
        path = os.environ.get("PATH", "/usr/bin:/bin")
        if self.agent_executable is not None:
            path = f"{self.agent_executable.parent}{os.pathsep}{path}"
        environment = {
            "PATH": path,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "TMPDIR": str(home / "tmp"),
            "LANG": "C.UTF-8",
        }
        if self.agent_executable is not None:
            environment["AGENTSKILL_EVAL_AGENT_EXECUTABLE"] = str(self.agent_executable)
        conflicts = _RESERVED_ENV.intersection(request.secret_env)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"secret_env cannot override runner-managed variables: {names}")
        environment.update(request.secret_env)
        for directory in (home / ".config", home / ".cache", home / "tmp"):
            directory.mkdir(parents=True, exist_ok=True)
        self._materialize_agent_home_files(request, home)
        return environment

    @staticmethod
    def _materialize_agent_home_files(request: RunnerRequest, home: Path) -> None:
        """Write declarative, non-secret Agent settings inside the isolated HOME."""
        home.mkdir(parents=True, exist_ok=True)
        if home.is_symlink():
            raise ValueError("isolated Agent HOME cannot be a symlink")
        root = home.resolve()
        secret_values = {value for value in request.secret_env.values() if value}
        for relative, payload in request.agent_home_files.items():
            path = Path(relative)
            if (
                not relative
                or _SAFE_HOME_PATH.fullmatch(relative) is None
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError(f"unsafe Agent HOME file path: {relative!r}")
            serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if any(secret in serialized for secret in secret_values):
                raise ValueError(f"Agent HOME file {relative!r} contains a Secret value")
            destination = (root / path).resolve()
            if not destination.is_relative_to(root):
                raise ValueError(f"Agent HOME file escapes isolated HOME: {relative!r}")
            current = root
            for part in path.parts[:-1]:
                current /= part
                if current.is_symlink():
                    raise ValueError(f"Agent HOME path contains a symlink: {relative!r}")
            if destination.is_symlink():
                raise ValueError(f"Agent HOME file cannot be a symlink: {relative!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(serialized, encoding="utf-8")
            destination.chmod(0o600)

    async def _version_report(self, request: RunnerRequest, home: Path) -> ValidationReport:
        outcome = await self._supervisor.run(
            f"{request.execution_id}:version",
            [str(self.binary), "--version"],
            request.run_dir,
            self._environment(request, home),
            10,
        )
        expected = f"skill-up version {SKILL_UP_VERSION}"
        valid = outcome.exit_code == 0 and expected in outcome.stdout
        return ValidationReport(
            valid=valid,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            errors=() if valid else (f"expected {expected!r}",),
        )

    async def validate(self, request: RunnerRequest) -> ValidationReport:
        try:
            self.verify_binary()
            compiled = compile_evaluation(request)
        except (IncompatibleRunnerError, CompilationError, OSError) as exc:
            return ValidationReport(valid=False, errors=(str(exc),))
        home = request.run_dir / "runner-home"
        try:
            version = await self._version_report(request, home)
        except (OSError, ValueError) as exc:
            return ValidationReport(valid=False, errors=(str(exc),))
        if not version.valid:
            return version
        try:
            outcome = await self._supervisor.run(
                f"{request.execution_id}:validate",
                [str(self.binary), "validate", str(compiled.eval_path)],
                compiled.root,
                self._environment(request, home),
                min(request.timeout_seconds, 30),
            )
        except (OSError, ValueError) as exc:
            return ValidationReport(valid=False, errors=(str(exc),))
        errors = () if outcome.exit_code == 0 else ("skill-up validate failed",)
        return ValidationReport(
            valid=outcome.exit_code == 0,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            errors=errors,
            skill_evidence=inspect_compiled_skill(compiled, request),
        )

    async def execute(
        self, request: RunnerRequest, event_sink: TraceEventSink = null_event_sink
    ) -> RunnerResult:
        await event_sink(
            RunnerEvent(request.execution_id, "runner.started", {"adapter": "skill-up"})
        )
        try:
            self.verify_binary()
            compiled = compile_evaluation(request)
        except (IncompatibleRunnerError, CompilationError, OSError) as exc:
            return self._error_result(request, ExitReason.CLI_ERROR, str(exc))
        home = request.run_dir / "runner-home"
        try:
            version = await self._version_report(request, home)
        except (OSError, ValueError) as exc:
            return self._error_result(request, ExitReason.CLI_ERROR, str(exc))
        if not version.valid:
            return self._error_result(request, ExitReason.CLI_ERROR, "; ".join(version.errors))
        try:
            outcome = await self._supervisor.run(
                request.execution_id,
                [
                    str(self.binary),
                    "run",
                    str(compiled.eval_path),
                    "--output-dir",
                    str(compiled.output_dir),
                    "--iteration",
                    "1",
                    "--format",
                    "json",
                ],
                compiled.root,
                self._environment(request, home),
                request.timeout_seconds + 15,
            )
        except (OSError, ValueError) as exc:
            return self._error_result(request, ExitReason.CLI_ERROR, str(exc))
        if outcome.cancelled:
            return self._error_result(
                request, ExitReason.CANCELLED, outcome.stderr, outcome.exit_code, outcome.stdout
            )
        if outcome.timed_out:
            return self._error_result(
                request, ExitReason.TIMEOUT, outcome.stderr, outcome.exit_code, outcome.stdout
            )
        result_path = compiled.output_dir / "iteration-1" / "result.json"
        if not result_path.is_file():
            return self._error_result(
                request,
                ExitReason.MISSING_REPORT,
                outcome.stderr,
                outcome.exit_code,
                outcome.stdout,
            )
        try:
            result = parse_skill_up_result(
                result_path,
                request.execution_id,
                request.case_id,
                outcome.exit_code,
                outcome.stdout,
                outcome.stderr,
            )
        except ResultParseError as exc:
            return self._error_result(
                request, ExitReason.CLI_ERROR, str(exc), outcome.exit_code, outcome.stdout
            )
        await self._emit_trace_events(result.raw_result, event_sink, request.execution_id)
        await event_sink(
            RunnerEvent(request.execution_id, "runner.finished", {"status": result.status.value})
        )
        return result

    @staticmethod
    async def _emit_trace_events(
        raw_result: Mapping[str, Any], event_sink: TraceEventSink, execution_id: str
    ) -> None:
        raw_events = raw_result.get("trace_events")
        if not isinstance(raw_events, list):
            return
        for item in raw_events[:10_000]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            payload = item.get("payload")
            if not isinstance(kind, str) or _TRACE_KIND.fullmatch(kind) is None:
                continue
            safe_payload = (
                {
                    key: value
                    for key, value in payload.items()
                    if str(key).lower() not in {"reasoning", "chain_of_thought", "thought"}
                }
                if isinstance(payload, dict)
                else {}
            )
            await event_sink(RunnerEvent(execution_id, kind, safe_payload))

    async def cancel(self, execution_id: str) -> bool:
        return await self._supervisor.cancel(execution_id)

    @staticmethod
    def _error_result(
        request: RunnerRequest,
        reason: ExitReason,
        stderr: str,
        process_exit_code: Optional[int] = None,
        stdout: str = "",
    ) -> RunnerResult:
        return RunnerResult(
            execution_id=request.execution_id,
            case_id=request.case_id,
            status=RunnerStatus.ERROR,
            exit_reason=reason,
            process_exit_code=process_exit_code,
            stdout=stdout,
            stderr=stderr,
        )


def discover_skill_up_binary() -> Optional[Path]:
    configured = os.environ.get("AGENTSKILL_EVAL_SKILL_UP_BIN")
    if configured:
        return Path(configured)
    found = shutil.which("skill-up")
    if found:
        return Path(found)
    managed = Path.home() / ".local/share/agentskill-eval/runners/skill-up/v0.5.0/skill-up"
    return managed if managed.is_file() else None
