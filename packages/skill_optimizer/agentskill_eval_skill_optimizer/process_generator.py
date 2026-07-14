"""Hash-pinned local Process boundary for auditable Skill improvement proposals."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

from pydantic import Field, model_validator

from agentskill_eval_contracts import FailureLabel, FrozenModel, canonical_json, stable_sha256


class ProcessGeneratorError(RuntimeError):
    """Raised when a proposal generator violates its frozen process contract."""


class HypothesisGeneratorSpec(FrozenModel):
    type: Literal["deterministic", "process", "deepseek"] = "deterministic"
    name: str = Field(default="failure-guidance", min_length=1)
    version: str = Field(min_length=1)
    max_hypotheses: int = Field(default=8, ge=3, le=20)
    executable: Optional[Path] = None
    expected_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    argv: Tuple[str, ...] = ()
    version_args: Tuple[str, ...] = ("--version",)
    expected_version_output: Optional[str] = None
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    max_request_bytes: int = Field(default=250_000, ge=1, le=2_000_000)
    max_response_bytes: int = Field(default=250_000, ge=1, le=2_000_000)
    max_json_depth: int = Field(default=20, ge=1, le=50)
    max_json_fields: int = Field(default=1_000, ge=1, le=10_000)
    allowed_environment: Tuple[str, ...] = ("PATH", "LANG", "LC_ALL")
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_output_tokens: int = Field(default=4_000, ge=256, le=20_000)
    secret_env_name: Optional[str] = None
    input_cache_miss_microusd_per_million: int = Field(default=435_000, ge=0)
    input_cache_hit_microusd_per_million: int = Field(default=3_625, ge=0)
    output_microusd_per_million: int = Field(default=870_000, ge=0)

    @model_validator(mode="after")
    def process_fields_and_environment_are_safe(self) -> "HypothesisGeneratorSpec":
        process_fields = (
            self.executable,
            self.expected_sha256,
            self.expected_version_output,
        )
        if self.type == "process" and any(value is None for value in process_fields):
            raise ValueError(
                "process generator requires executable, expected_sha256 and expected_version_output"
            )
        if self.type != "process" and any(value is not None for value in process_fields):
            raise ValueError("non-process generator cannot declare process fields")
        if self.type == "process" and not str(self.expected_version_output).strip():
            raise ValueError("process generator expected_version_output must not be empty")
        deepseek_fields = (self.base_url, self.model, self.secret_env_name)
        if self.type == "deepseek" and any(value is None for value in deepseek_fields):
            raise ValueError("deepseek generator requires base_url, model and secret_env_name")
        if self.type != "deepseek" and any(value is not None for value in deepseek_fields):
            raise ValueError("non-deepseek generator cannot declare DeepSeek fields")
        if self.type == "deepseek":
            base_url = str(self.base_url).rstrip("/")
            if not (
                base_url == "https://api.deepseek.com"
                or base_url.startswith("http://127.0.0.1:")
                or base_url.startswith("http://localhost:")
            ):
                raise ValueError("deepseek base_url must be official HTTPS or local Fake API")
            if self.secret_env_name != "OPENAI_API_KEY":
                raise ValueError("deepseek generator secret_env_name must be OPENAI_API_KEY")
            if not str(self.model).startswith("deepseek-"):
                raise ValueError("deepseek generator model must be a DeepSeek model")
            if self.max_hypotheses > 5:
                raise ValueError("deepseek generator supports at most five hypotheses per call")
        if len(set(self.allowed_environment)) != len(self.allowed_environment):
            raise ValueError("allowed_environment values must be unique")
        secret_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
        if any(
            marker in name.upper() for name in self.allowed_environment for marker in secret_markers
        ):
            raise ValueError("Process Generator cannot inherit Secret-like environment names")
        safe_environment = {
            "PATH",
            "LANG",
            "LC_ALL",
            "TZ",
            "PYTHONHASHSEED",
            # Deterministic integration-fixture controls; neither carries credentials.
            "ASE_PROCESS_GENERATOR_COUNTER",
            "ASE_FAKE_GENERATOR_MODE",
        }
        unknown = set(self.allowed_environment) - safe_environment
        if unknown:
            raise ValueError(
                "Process Generator environment is not on the minimal allowlist: "
                + ", ".join(sorted(unknown))
            )
        return self


class ProcessHypothesisProposal(FrozenModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    failure_label: FailureLabel
    hypothesis: str = Field(min_length=10, max_length=2_000)
    instruction: str = Field(min_length=10, max_length=4_000)
    risks: Tuple[str, ...] = Field(default=(), max_length=10)


class ProcessHypothesisResponse(FrozenModel):
    schema_version: Literal["ase/process-hypothesis-response/v1alpha1"]
    hypotheses: Tuple[ProcessHypothesisProposal, ...] = Field(min_length=3, max_length=20)

    @model_validator(mode="after")
    def ids_must_be_unique(self) -> "ProcessHypothesisResponse":
        ids = [item.id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("Process Generator hypothesis IDs must be unique")
        return self


class GeneratorInvocationEvidence(FrozenModel):
    schema_version: Literal["ase/process-generator-evidence/v1alpha1"] = (
        "ase/process-generator-evidence/v1alpha1"
    )
    generator_name: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_verified: Literal[True] = True
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypotheses_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_count: int = Field(ge=3, le=20)
    duration_ms: float = Field(ge=0)
    exit_code: Literal[0] = 0
    inherited_environment: Tuple[str, ...]
    stderr_stored: Literal[False] = False
    raw_request_stored: Literal[False] = False
    raw_response_stored: Literal[False] = False
    hidden_reasoning_stored: Literal[False] = False


class ProcessGenerationResult(FrozenModel):
    proposals: Tuple[ProcessHypothesisProposal, ...]
    evidence: GeneratorInvocationEvidence


class ProcessHypothesisGenerator:
    """Invoke one local proposal process without shell, Secrets, or validation inputs."""

    def __init__(self, spec: HypothesisGeneratorSpec) -> None:
        if spec.type != "process" or spec.executable is None or spec.expected_sha256 is None:
            raise ValueError("ProcessHypothesisGenerator requires a complete process spec")
        self.spec = spec
        self.executable_sha256 = spec.expected_sha256
        expanded = spec.executable.expanduser()
        if expanded.is_symlink():
            raise ValueError("Process Generator executable must not be a symlink")
        self.executable = expanded.resolve(strict=True)
        if not self.executable.is_file():
            raise ValueError("Process Generator executable must be a regular file")
        digest = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        if digest != self.executable_sha256:
            raise ValueError("Process Generator executable SHA-256 mismatch")

    @property
    def identity(self) -> str:
        return (
            f"process-hypothesis-generator:{self.spec.name}@{self.spec.version}:"
            f"{self.executable_sha256}"
        )

    def generate(
        self,
        request: Mapping[str, object],
        eligible_labels: Tuple[FailureLabel, ...],
    ) -> ProcessGenerationResult:
        request_bytes = canonical_json(dict(request))
        if len(request_bytes) > self.spec.max_request_bytes:
            raise ProcessGeneratorError("Process Generator request is too large")
        self._verify_version()
        started = time.monotonic()
        stdout, return_code = self._communicate(request_bytes + b"\n")
        duration_ms = (time.monotonic() - started) * 1000
        if return_code != 0:
            raise ProcessGeneratorError(f"Process Generator exited with code {return_code}")
        if len(stdout) > self.spec.max_response_bytes:
            raise ProcessGeneratorError("Process Generator response is too large")
        try:
            raw = json.loads(stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProcessGeneratorError("Process Generator returned invalid JSON") from exc
        if not _json_within_limits(raw, self.spec.max_json_depth, self.spec.max_json_fields):
            raise ProcessGeneratorError("Process Generator response exceeds JSON limits")
        try:
            response = ProcessHypothesisResponse.model_validate(raw)
        except ValueError as exc:
            raise ProcessGeneratorError(f"invalid Process Generator response: {exc}") from exc
        if len(response.hypotheses) > self.spec.max_hypotheses:
            raise ProcessGeneratorError("Process Generator exceeded max_hypotheses")
        allowed = set(eligible_labels)
        if any(item.failure_label not in allowed for item in response.hypotheses):
            raise ProcessGeneratorError("Process Generator used an ineligible failure label")
        if hashlib.sha256(self.executable.read_bytes()).hexdigest() != self.executable_sha256:
            raise ProcessGeneratorError("Process Generator executable changed during invocation")
        hypotheses_payload = [item.model_dump(mode="json") for item in response.hypotheses]
        evidence = GeneratorInvocationEvidence(
            generator_name=self.spec.name,
            generator_version=self.spec.version,
            executable_sha256=self.executable_sha256,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            response_sha256=stable_sha256(raw),
            hypotheses_sha256=stable_sha256(hypotheses_payload),
            hypothesis_count=len(response.hypotheses),
            duration_ms=duration_ms,
            inherited_environment=tuple(
                name for name in self.spec.allowed_environment if name in os.environ
            ),
        )
        return ProcessGenerationResult(proposals=response.hypotheses, evidence=evidence)

    def _verify_version(self) -> None:
        if self.spec.expected_version_output is None:
            raise ProcessGeneratorError("Process Generator expected version output is missing")
        stdout, return_code = self._communicate(b"", version=True)
        if len(stdout) > self.spec.max_response_bytes:
            raise ProcessGeneratorError("Process Generator version response is too large")
        observed = stdout.decode("utf-8", errors="replace").strip()
        if return_code != 0 or observed != self.spec.expected_version_output:
            raise ProcessGeneratorError("Process Generator version mismatch")

    def _communicate(self, stdin: bytes, *, version: bool = False) -> Tuple[bytes, int]:
        args = [str(self.executable), *self.spec.argv]
        if version:
            args.extend(self.spec.version_args)
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._environment(),
                start_new_session=True,
            )
            stdout, _stderr = process.communicate(stdin, timeout=self.spec.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ProcessGeneratorError("Process Generator timed out") from exc
        except OSError as exc:
            raise ProcessGeneratorError(f"Process Generator could not start: {exc}") from exc
        except BaseException:
            if "process" in locals() and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise
        return stdout, process.returncode

    def _environment(self) -> Dict[str, str]:
        return {
            name: os.environ[name] for name in self.spec.allowed_environment if name in os.environ
        }


def _json_within_limits(value: Any, max_depth: int, max_fields: int) -> bool:
    fields = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal fields
        if depth > max_depth:
            return False
        if isinstance(item, dict):
            fields += len(item)
            return fields <= max_fields and all(visit(child, depth + 1) for child in item.values())
        if isinstance(item, list):
            return all(visit(child, depth + 1) for child in item)
        return True

    return visit(value, 1)
