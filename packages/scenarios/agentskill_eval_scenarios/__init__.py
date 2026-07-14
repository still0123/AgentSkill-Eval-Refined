"""Unified multi-scenario evaluation facade."""

from agentskill_eval_scenarios.adapters import ScenarioAdapter
from agentskill_eval_scenarios.contracts import (
    ArtifactReference,
    ComparisonKind,
    EvaluationPlan,
    EvidenceClass,
    ProcessScenarioAgentSpec,
    ScenarioKind,
    SkillUnderTest,
    UnifiedEvaluationResult,
    UnifiedScenarioSpec,
    VariantDescriptor,
)
from agentskill_eval_scenarios.interactive import (
    InteractionHistoryEvent,
    InteractiveAgentAction,
    InteractiveRunEvidence,
    InteractiveTraceEvent,
)
from agentskill_eval_scenarios.process_agent import (
    AgentDecisionEvidence,
    ProcessAgentError,
    ProcessScenarioAgentClient,
)
from agentskill_eval_scenarios.runtime import UnifiedScenarioRunner

__all__ = [
    "ArtifactReference",
    "ComparisonKind",
    "EvaluationPlan",
    "EvidenceClass",
    "AgentDecisionEvidence",
    "InteractionHistoryEvent",
    "InteractiveAgentAction",
    "InteractiveRunEvidence",
    "InteractiveTraceEvent",
    "ProcessAgentError",
    "ProcessScenarioAgentClient",
    "ProcessScenarioAgentSpec",
    "ScenarioAdapter",
    "ScenarioKind",
    "SkillUnderTest",
    "UnifiedEvaluationResult",
    "UnifiedScenarioRunner",
    "UnifiedScenarioSpec",
    "VariantDescriptor",
]
