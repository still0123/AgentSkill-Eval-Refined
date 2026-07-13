"""Dataset ingestion and auditable benchmark generation primitives."""

from agentskill_eval_benchmark_gen.dataset import (
    CaseCategory,
    CaseGroupKeys,
    CaseMetadata,
    CaseOracle,
    CaseProvenance,
    DatasetLoader,
    DatasetManifest,
    DatasetSplit,
    LoadedCase,
    LoadedDataset,
)
from agentskill_eval_benchmark_gen.demo import (
    DemoExperimentRunner,
    DemoMode,
    DemoRunConfig,
    DemoRunResult,
)
from agentskill_eval_benchmark_gen.generator import (
    AutomaticBenchmarkGenerator,
    BenchmarkGenerationError,
    BenchmarkStore,
    GenerationResult,
)
from agentskill_eval_benchmark_gen.spec import (
    BenchmarkGenerationSpec,
    BudgetSpec,
    CandidateSpec,
    QualityGateSpec,
    SpecError,
)

__all__ = [
    "AutomaticBenchmarkGenerator",
    "BenchmarkGenerationError",
    "BenchmarkGenerationSpec",
    "BenchmarkStore",
    "BudgetSpec",
    "CaseCategory",
    "CaseGroupKeys",
    "CaseMetadata",
    "CaseOracle",
    "CaseProvenance",
    "CandidateSpec",
    "DatasetLoader",
    "DatasetManifest",
    "DatasetSplit",
    "DemoExperimentRunner",
    "DemoMode",
    "DemoRunConfig",
    "DemoRunResult",
    "LoadedCase",
    "LoadedDataset",
    "GenerationResult",
    "QualityGateSpec",
    "SpecError",
]
