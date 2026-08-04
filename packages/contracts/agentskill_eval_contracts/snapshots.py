"""Immutable configuration and observed-environment snapshots."""

from __future__ import annotations

from typing import Dict, Optional
from uuid import UUID

from pydantic import Field, JsonValue

from agentskill_eval_contracts.base import FrozenModel, HexDigest


class RunnerSnapshot(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    binary_sha256: HexDigest
    config: Dict[str, JsonValue] = Field(default_factory=dict)


class AgentSnapshot(FrozenModel):
    engine: str = Field(min_length=1)
    engine_version: Optional[str] = None
    model: str = Field(min_length=1)
    generation_parameters: Dict[str, JsonValue] = Field(default_factory=dict)


class SkillSnapshot(FrozenModel):
    skill_id: UUID
    version_id: UUID
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_sha256: HexDigest
    injection_mode: str = Field(min_length=1)


class ToolSnapshot(FrozenModel):
    definitions_sha256: Optional[HexDigest] = None
    config: Dict[str, JsonValue] = Field(default_factory=dict)


class SandboxSnapshot(FrozenModel):
    profile: str = Field(min_length=1)
    image: Optional[str] = None
    image_digest: Optional[str] = None
    network_policy: str = Field(default="none", min_length=1)
    resource_limits: Dict[str, JsonValue] = Field(default_factory=dict)


class PriceSnapshot(FrozenModel):
    currency: str = Field(default="USD", min_length=3, max_length=3)
    table_sha256: HexDigest
    rates: Dict[str, JsonValue] = Field(default_factory=dict)


class EnvironmentFingerprint(FrozenModel):
    """Best-effort observed environment; unavailable fields remain null."""

    platform_revision: str = Field(min_length=1)
    runner_version: str = Field(min_length=1)
    runner_binary_sha256: HexDigest
    agent_engine: str = Field(min_length=1)
    agent_version: Optional[str] = None
    provider: Optional[str] = None
    deployment: Optional[str] = None
    model_revision: Optional[str] = None
    request_id: Optional[str] = None
    region: Optional[str] = None
    image_digest: Optional[str] = None
    runtime_dependencies: Dict[str, str] = Field(default_factory=dict)
    unavailable_reasons: Dict[str, str] = Field(default_factory=dict)
