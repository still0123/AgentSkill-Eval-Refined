"""Immutable observable measurements captured from one physical RunAttempt."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import Field, computed_field

from agentskill_eval_contracts.base import FrozenModel


class RunMeasurement(FrozenModel):
    run_id: UUID
    attempt_id: UUID
    runner_status: Literal["PASS", "FAIL", "SKIP", "ERROR"]
    runner_exit_reason: str = Field(min_length=1)
    process_exit_code: Optional[int] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    turns: Optional[int] = Field(default=None, ge=0)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    cached_input_tokens: Optional[int] = Field(default=None, ge=0)
    tool_calls: Optional[int] = Field(default=None, ge=0)
    cost_microusd: Optional[int] = Field(default=None, ge=0)

    @computed_field(return_type=Optional[int])  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> Optional[int]:
        if self.input_tokens is not None and self.output_tokens is not None:
            return self.input_tokens + self.output_tokens
        return None

    
