"""Single-call DeepSeek proposal generator using the audited Process request contract."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Literal, Mapping, Optional, Protocol, Tuple

from pydantic import Field

from agentskill_eval_contracts import FailureLabel, FrozenModel, canonical_json, stable_sha256
from agentskill_eval_skill_optimizer.process_generator import (
    HypothesisGeneratorSpec,
    ProcessHypothesisProposal,
    ProcessHypothesisResponse,
)


class DeepSeekGeneratorError(RuntimeError):
    """Raised when the DeepSeek proposal call or response violates its frozen contract."""


@dataclass
class DeepSeekGeneratorAuthorization:
    confirm_real_run: bool
    max_calls: int
    max_cost_microusd: int
    calls_consumed: int = 0
    observed_or_reserved_cost_microusd: int = 0

    def reserve(self, estimated_cost_microusd: int) -> None:
        if not self.confirm_real_run:
            raise DeepSeekGeneratorError("DeepSeek proposal generation requires confirmation")
        if self.calls_consumed + 1 > self.max_calls:
            raise DeepSeekGeneratorError("DeepSeek proposal call budget exhausted")
        if (
            self.observed_or_reserved_cost_microusd + estimated_cost_microusd
            > self.max_cost_microusd
        ):
            raise DeepSeekGeneratorError("DeepSeek proposal cost budget exhausted")
        self.calls_consumed += 1
        self.observed_or_reserved_cost_microusd += estimated_cost_microusd

    def settle(self, estimated_cost_microusd: int, actual_cost_microusd: int) -> None:
        self.observed_or_reserved_cost_microusd += actual_cost_microusd - estimated_cost_microusd


class DeepSeekGeneratorInvocationEvidence(FrozenModel):
    schema_version: Literal["ase/deepseek-generator-evidence/v1alpha1"] = (
        "ase/deepseek-generator-evidence/v1alpha1"
    )
    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypotheses_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_count: int = Field(ge=3, le=5)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    finish_reason: Literal["stop"] = "stop"
    raw_request_stored: Literal[False] = False
    raw_response_stored: Literal[False] = False
    hidden_reasoning_stored: Literal[False] = False
    secret_value_stored: Literal[False] = False


class DeepSeekGenerationResult(FrozenModel):
    proposals: Tuple[ProcessHypothesisProposal, ...]
    evidence: DeepSeekGeneratorInvocationEvidence


class DeepSeekTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: bytes,
        timeout_seconds: float,
    ) -> bytes: ...


class UrllibDeepSeekTransport:
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: bytes,
        timeout_seconds: float,
    ) -> bytes:
        request = urllib.request.Request(
            url,
            data=payload,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                return bytes(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise DeepSeekGeneratorError("DeepSeek proposal API request failed") from exc


_OUTPUT_SCHEMA = {
    "schema_version": "ase/process-hypothesis-response/v1alpha1",
    "hypotheses": [
        {
            "id": "short-hyphenated-id",
            "failure_label": "one eligible failure label from the input",
            "hypothesis": "testable explanation of why this Skill change may help",
            "instruction": "concise reusable instruction to append to SKILL.md",
            "risks": ["specific possible regression or cost"],
        }
    ],
}

_SYSTEM_PROMPT = """You improve an Agent Skill from audited train failures.
Return one JSON object only. Produce exactly the requested number of distinct hypotheses using
the supplied JSON schema shape. The `hypotheses` array must contain exactly
`required_hypothesis_count` objects (for this request, four objects). Every object MUST include
all five fields: `id`, `failure_label`, `hypothesis`, `instruction`, and `risks`. The `id` is
required, must be a short lowercase hyphenated identifier, and all ids must be unique; never
omit it or replace it with a number. Before returning, count the array and check that every
object has a non-empty unique id.
Every hypothesis must use an eligible failure_label and must
address the observed failure summary, not merely add generic logging or reporting advice.
Describe a general reusable change that would prevent the observed Agent behavior, keep the
instruction concise and imperative, and include concrete risks. Prefer changes to task
interpretation, planning, tool selection/arguments, recovery, or verification workflow when
those are supported by the observed summary. A candidate must be useful even when the exact
repository and case are unknown.
Do not mention case IDs, repository names, tests, patches, expected answers, validation data,
hidden reasoning, or facts not present in the train input. Do not copy failure evidence verbatim.
"""


class DeepSeekHypothesisGenerator:
    """Generate three to five structured proposals in one explicitly authorized API call."""

    def __init__(
        self,
        spec: HypothesisGeneratorSpec,
        authorization: Optional[DeepSeekGeneratorAuthorization],
        transport: Optional[DeepSeekTransport] = None,
    ) -> None:
        if spec.type != "deepseek" or not spec.base_url or not spec.model:
            raise ValueError("DeepSeekHypothesisGenerator requires a complete deepseek spec")
        self.spec = spec
        self.authorization = authorization
        self.transport = transport or UrllibDeepSeekTransport()

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest()

    @property
    def output_schema_sha256(self) -> str:
        return stable_sha256(_OUTPUT_SCHEMA)

    @property
    def identity(self) -> str:
        return (
            f"deepseek-hypothesis-generator:{self.spec.model}@{self.spec.version}:"
            f"{self.prompt_sha256}:{self.output_schema_sha256}"
        )

    def estimate_call_cost_microusd(self, request: Mapping[str, object]) -> int:
        request_payload = self._api_payload(request)
        request_bytes = canonical_json(request_payload)
        if len(request_bytes) > self.spec.max_request_bytes:
            raise DeepSeekGeneratorError("DeepSeek proposal request is too large")
        # One UTF-8 byte per token is deliberately conservative for the authorization gate.
        input_tokens_upper_bound = len(request_bytes)
        return self._cost(
            input_tokens=input_tokens_upper_bound,
            cached_input_tokens=0,
            output_tokens=self.spec.max_output_tokens,
        )

    def request_sha256(self, request: Mapping[str, object]) -> str:
        return hashlib.sha256(canonical_json(self._api_payload(request))).hexdigest()

    def generate(
        self,
        request: Mapping[str, object],
        eligible_labels: Tuple[FailureLabel, ...],
    ) -> DeepSeekGenerationResult:
        if self.spec.secret_env_name is None:
            raise DeepSeekGeneratorError("DeepSeek proposal Secret environment name is missing")
        api_payload = self._api_payload(request)
        request_bytes = canonical_json(api_payload)
        if len(request_bytes) > self.spec.max_request_bytes:
            raise DeepSeekGeneratorError("DeepSeek proposal request is too large")
        # One UTF-8 byte per token is deliberately conservative for the authorization gate.
        estimated_cost = self._cost(
            input_tokens=len(request_bytes),
            cached_input_tokens=0,
            output_tokens=self.spec.max_output_tokens,
        )
        if self.authorization is None:
            raise DeepSeekGeneratorError(
                "DeepSeek proposal generation requires confirmation and budget limits"
            )
        secret = os.environ.get(self.spec.secret_env_name)
        if not secret:
            raise DeepSeekGeneratorError(
                f"DeepSeek proposal Secret {self.spec.secret_env_name} is not set"
            )
        self.authorization.reserve(estimated_cost)
        started = time.monotonic()
        response_bytes = self.transport.post(
            f"{str(self.spec.base_url).rstrip('/')}/chat/completions",
            {
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            request_bytes,
            self.spec.timeout_seconds,
        )
        duration_ms = (time.monotonic() - started) * 1000
        if len(response_bytes) > self.spec.max_response_bytes:
            raise DeepSeekGeneratorError("DeepSeek proposal response is too large")
        try:
            raw = json.loads(response_bytes.decode("utf-8"))
            choice = raw["choices"][0]
            finish_reason = choice["finish_reason"]
            content = choice["message"]["content"]
            usage = raw["usage"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DeepSeekGeneratorError(
                "DeepSeek proposal API returned an invalid response"
            ) from exc
        if finish_reason != "stop":
            raise DeepSeekGeneratorError(
                f"DeepSeek proposal generation did not finish cleanly: {finish_reason}"
            )
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekGeneratorError("DeepSeek proposal API returned empty content")
        try:
            response = ProcessHypothesisResponse.model_validate(json.loads(content))
        except (ValueError, json.JSONDecodeError) as exc:
            raise DeepSeekGeneratorError(
                "DeepSeek proposal content is not valid structured JSON"
            ) from exc
        if len(response.hypotheses) > self.spec.max_hypotheses:
            raise DeepSeekGeneratorError("DeepSeek proposal response exceeded max_hypotheses")
        allowed = set(eligible_labels)
        if any(item.failure_label not in allowed for item in response.hypotheses):
            raise DeepSeekGeneratorError("DeepSeek proposal used an ineligible failure label")
        input_tokens = self._usage_int(usage, "prompt_tokens")
        cached_input_tokens = self._usage_int(usage, "prompt_cache_hit_tokens")
        output_tokens = self._usage_int(usage, "completion_tokens")
        reasoning_tokens = 0
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens_details"), dict):
            reasoning_tokens = self._usage_int(
                usage["completion_tokens_details"], "reasoning_tokens"
            )
        actual_cost = self._cost(input_tokens, cached_input_tokens, output_tokens)
        self.authorization.settle(estimated_cost, actual_cost)
        proposals_payload = [item.model_dump(mode="json") for item in response.hypotheses]
        evidence = DeepSeekGeneratorInvocationEvidence(
            model=str(self.spec.model),
            generator_version=self.spec.version,
            prompt_sha256=self.prompt_sha256,
            output_schema_sha256=self.output_schema_sha256,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            response_sha256=stable_sha256(raw),
            hypotheses_sha256=stable_sha256(proposals_payload),
            hypothesis_count=len(response.hypotheses),
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_microusd=actual_cost,
            duration_ms=duration_ms,
        )
        return DeepSeekGenerationResult(proposals=response.hypotheses, evidence=evidence)

    def _api_payload(self, request: Mapping[str, object]) -> Dict[str, object]:
        user_payload = {
            "request": dict(request),
            "required_hypothesis_count": self.spec.max_hypotheses,
            "json_output_schema_example": _OUTPUT_SCHEMA,
        }
        return {
            "model": self.spec.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": canonical_json(user_payload).decode("utf-8"),
                },
            ],
            "temperature": self.spec.temperature,
            "max_tokens": self.spec.max_output_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }

    def _cost(self, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> int:
        cache_hit = min(input_tokens, cached_input_tokens)
        cache_miss = input_tokens - cache_hit
        numerator = (
            cache_miss * self.spec.input_cache_miss_microusd_per_million
            + cache_hit * self.spec.input_cache_hit_microusd_per_million
            + output_tokens * self.spec.output_microusd_per_million
        )
        return (numerator + 999_999) // 1_000_000

    @staticmethod
    def _usage_int(usage: object, key: str) -> int:
        if not isinstance(usage, dict):
            raise DeepSeekGeneratorError("DeepSeek proposal response has invalid usage")
        value = usage.get(key, 0)
        if not isinstance(value, int) or value < 0:
            raise DeepSeekGeneratorError("DeepSeek proposal response has invalid usage")
        return value
