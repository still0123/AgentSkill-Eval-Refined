"""Paired, group-aware statistics for completed local experiments."""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import AbstractSet, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID

from pydantic import Field

from agentskill_eval_contracts import (
    EvaluationOutcome,
    ExecutionStatus,
    FrozenModel,
    PairBlock,
    Run,
    RunMeasurement,
)
from agentskill_eval_experiment.storage import LocalExperimentStore

ValueExtractor = Callable[[Optional[RunMeasurement]], Optional[float]]


class StatisticsError(ValueError):
    """Raised when persisted evidence cannot support the requested analysis."""


class ConfidenceInterval(FrozenModel):
    low: float
    high: float
    confidence_level: float = 0.95


class EstimandSummary(FrozenModel):
    control_pass_rate: Optional[float]
    treatment_pass_rate: Optional[float]
    absolute_gain: Optional[float]
    relative_gain: Optional[float]
    control_ci: Optional[ConfidenceInterval] = None
    treatment_ci: Optional[ConfidenceInterval] = None
    gain_ci: Optional[ConfidenceInterval] = None
    included_cases: int = Field(ge=0)
    included_groups: int = Field(ge=0)


class WtlSummary(FrozenModel):
    win: int = Field(ge=0)
    tie_positive: int = Field(ge=0)
    tie_negative: int = Field(ge=0)
    loss: int = Field(ge=0)
    invalid: int = Field(default=0, ge=0)


class CaseComparison(FrozenModel):
    case_id: UUID
    independence_group: str
    control_pass_rate: float
    treatment_pass_rate: float
    absolute_gain: float
    classification: str


class EfficiencyComparison(FrozenModel):
    unit: str
    control_mean: Optional[float]
    treatment_mean: Optional[float]
    relative_overhead: Optional[float]
    relative_overhead_ci: Optional[ConfidenceInterval] = None
    paired_median_delta: Optional[float]
    paired_median_delta_ci: Optional[ConfidenceInterval] = None
    observed_control_runs: int = Field(ge=0)
    observed_treatment_runs: int = Field(ge=0)
    observed_pairs: int = Field(ge=0)


class VariantRunSummary(FrozenModel):
    variant_id: UUID
    assigned_runs: int = Field(ge=0)
    pass_runs: int = Field(ge=0)
    fail_runs: int = Field(ge=0)
    invalid_runs: int = Field(ge=0)
    total_cost_microusd: Optional[int] = Field(default=None, ge=0)
    cost_per_success_microusd: Optional[float] = Field(default=None, ge=0)


class ExperimentStatistics(FrozenModel):
    experiment_id: UUID
    control_variant_id: UUID
    treatment_variant_id: UUID
    run_count: int = Field(ge=0)
    case_count: int = Field(ge=0)
    independence_group_count: int = Field(ge=0)
    complete_block_ratio: float = Field(ge=0, le=1)
    valid_block_ratio: float = Field(ge=0, le=1)
    invalid_case_count: int = Field(default=0, ge=0)
    inference_ready: bool
    inference_note: str
    bootstrap_resamples: int = Field(ge=1)
    bootstrap_seed: int
    majority_threshold: float = Field(gt=0, le=1)
    min_independent_groups: int = Field(ge=1)
    weighting: str = "equal_independence_group"
    primary_assignment_based: EstimandSummary
    sensitivity_capability: EstimandSummary
    wtl: WtlSummary
    cases: Tuple[CaseComparison, ...]
    variants: Tuple[VariantRunSummary, ...]
    tokens: EfficiencyComparison
    latency_ms: EfficiencyComparison
    cost_microusd: EfficiencyComparison


@dataclass(frozen=True)
class AnalysisConfig:
    control_variant_id: UUID
    treatment_variant_id: UUID
    bootstrap_resamples: int = 10_000
    bootstrap_seed: int = 2026
    majority_threshold: float = 0.5
    min_independent_groups: int = 2


@dataclass(frozen=True)
class _Datum:
    block: PairBlock
    run: Run
    measurement: Optional[RunMeasurement]

    @property
    def conservative_success(self) -> float:
        return 1.0 if self.run.evaluation_outcome == EvaluationOutcome.PASS else 0.0

    @property
    def is_valid(self) -> bool:
        return self.run.evaluation_outcome in {EvaluationOutcome.PASS, EvaluationOutcome.FAIL}


class ExperimentAnalyzer:
    def __init__(self, store: LocalExperimentStore) -> None:
        self.store = store

    def analyze(self, experiment_id: UUID, config: AnalysisConfig) -> ExperimentStatistics:
        if config.bootstrap_resamples < 1:
            raise StatisticsError("bootstrap_resamples must be positive")
        if not 0 < config.majority_threshold <= 1:
            raise StatisticsError("majority_threshold must be in (0, 1]")
        if config.control_variant_id == config.treatment_variant_id:
            raise StatisticsError("control and treatment variants must differ")

        blocks = self.store.list_pair_blocks(experiment_id)
        runs = self.store.list_runs(experiment_id)
        if not blocks:
            raise StatisticsError("experiment has no PairBlocks")
        block_by_id = {block.id: block for block in blocks}
        target_ids = {config.control_variant_id, config.treatment_variant_id}
        datums: Dict[Tuple[UUID, UUID], _Datum] = {}
        for run in runs:
            if run.variant_id not in target_ids:
                continue
            block = block_by_id.get(run.pair_block_id)
            if block is None:
                raise StatisticsError(f"run {run.id} references a missing PairBlock")
            if run.execution_status not in {
                ExecutionStatus.COMPLETED,
                ExecutionStatus.INFRA_FAILED,
                ExecutionStatus.CANCELLED,
            }:
                raise StatisticsError(f"run {run.id} is not terminal")
            key = (run.pair_block_id, run.variant_id)
            if key in datums:
                raise StatisticsError("duplicate Run for PairBlock and Variant")
            datums[key] = _Datum(
                block,
                run,
                self.store.load_selected_measurement(experiment_id, run),
            )

        complete_blocks = []
        valid_blocks = []
        for block in blocks:
            pair = [datums.get((block.id, variant_id)) for variant_id in target_ids]
            if all(item is not None for item in pair):
                complete_blocks.append(block)
                if all(item is not None and item.is_valid for item in pair):
                    valid_blocks.append(block)
            else:
                raise StatisticsError(f"PairBlock {block.id} is missing a target Variant Run")

        primary_cases = self._case_rates(blocks, datums, config, valid_only=False)
        capability_cases = self._case_rates(valid_blocks, datums, config, valid_only=True)
        primary = self._estimand(primary_cases, config)
        capability = self._estimand(capability_cases, config)
        invalid_case_ids = {
            block.case_id
            for block in blocks
            if not (
                datums[(block.id, config.control_variant_id)].is_valid
                and datums[(block.id, config.treatment_variant_id)].is_valid
            )
        }
        comparisons = self._case_comparisons(
            primary_cases,
            config.majority_threshold,
            invalid_case_ids=invalid_case_ids,
        )
        groups = {block.independence_group for block in blocks}
        inference_ready = (
            not invalid_case_ids and len(groups) >= config.min_independent_groups
        )
        if invalid_case_ids:
            note = "descriptive only: invalid observations are present"
        elif inference_ready:
            note = "confirmatory group threshold satisfied"
        else:
            note = "descriptive only: too few independent groups"
        return ExperimentStatistics(
            experiment_id=experiment_id,
            control_variant_id=config.control_variant_id,
            treatment_variant_id=config.treatment_variant_id,
            run_count=len(datums),
            case_count=len(primary_cases),
            independence_group_count=len(groups),
            complete_block_ratio=len(complete_blocks) / len(blocks),
            valid_block_ratio=len(valid_blocks) / len(blocks),
            invalid_case_count=len(invalid_case_ids),
            inference_ready=inference_ready,
            inference_note=note,
            bootstrap_resamples=config.bootstrap_resamples,
            bootstrap_seed=config.bootstrap_seed,
            majority_threshold=config.majority_threshold,
            min_independent_groups=config.min_independent_groups,
            primary_assignment_based=primary,
            sensitivity_capability=capability,
            wtl=self._wtl(comparisons),
            cases=tuple(comparisons),
            variants=tuple(
                self._variant_summary(variant_id, datums.values())
                for variant_id in (config.control_variant_id, config.treatment_variant_id)
            ),
            tokens=self._efficiency("tokens", datums, config, self._tokens),
            latency_ms=self._efficiency("milliseconds", datums, config, self._latency),
            cost_microusd=self._efficiency("micro-USD", datums, config, self._cost),
        )

    @staticmethod
    def _case_rates(
        blocks: Sequence[PairBlock],
        datums: Mapping[Tuple[UUID, UUID], _Datum],
        config: AnalysisConfig,
        *,
        valid_only: bool,
    ) -> Dict[UUID, Tuple[str, float, float]]:
        values: Dict[UUID, Tuple[str, List[float], List[float]]] = {}
        for block in blocks:
            control = datums[(block.id, config.control_variant_id)]
            treatment = datums[(block.id, config.treatment_variant_id)]
            if valid_only and not (control.is_valid and treatment.is_valid):
                continue
            group, control_values, treatment_values = values.setdefault(
                block.case_id, (block.independence_group, [], [])
            )
            if group != block.independence_group:
                raise StatisticsError("one Case cannot belong to multiple independence groups")
            control_values.append(control.conservative_success)
            treatment_values.append(treatment.conservative_success)
        return {
            case_id: (group, statistics.fmean(control), statistics.fmean(treatment))
            for case_id, (group, control, treatment) in values.items()
            if control and treatment
        }

    def _estimand(
        self,
        cases: Mapping[UUID, Tuple[str, float, float]],
        config: AnalysisConfig,
    ) -> EstimandSummary:
        if not cases:
            return EstimandSummary(
                control_pass_rate=None,
                treatment_pass_rate=None,
                absolute_gain=None,
                relative_gain=None,
                included_cases=0,
                included_groups=0,
            )
        grouped = self._grouped_pairs(cases)
        control, treatment, gain = self._point_estimate(grouped)
        draws = self._bootstrap(grouped, config.bootstrap_resamples, config.bootstrap_seed)
        relative = None if control == 0 else gain / control
        return EstimandSummary(
            control_pass_rate=control,
            treatment_pass_rate=treatment,
            absolute_gain=gain,
            relative_gain=relative,
            control_ci=self._ci([draw[0] for draw in draws]),
            treatment_ci=self._ci([draw[1] for draw in draws]),
            gain_ci=self._ci([draw[2] for draw in draws]),
            included_cases=len(cases),
            included_groups=len(grouped),
        )

    @staticmethod
    def _grouped_pairs(
        cases: Mapping[UUID, Tuple[str, float, float]]
    ) -> Dict[str, List[Tuple[float, float]]]:
        grouped: Dict[str, List[Tuple[float, float]]] = {}
        for group, control, treatment in cases.values():
            grouped.setdefault(group, []).append((control, treatment))
        return grouped

    @staticmethod
    def _point_estimate(
        grouped: Mapping[str, Sequence[Tuple[float, float]]]
    ) -> Tuple[float, float, float]:
        group_control = [statistics.fmean(item[0] for item in cases) for cases in grouped.values()]
        group_treatment = [
            statistics.fmean(item[1] for item in cases) for cases in grouped.values()
        ]
        control = statistics.fmean(group_control)
        treatment = statistics.fmean(group_treatment)
        return control, treatment, treatment - control

    @staticmethod
    def _bootstrap(
        grouped: Mapping[str, Sequence[Tuple[float, float]]],
        resamples: int,
        seed: int,
    ) -> List[Tuple[float, float, float]]:
        rng = random.Random(seed)
        names = sorted(grouped)
        draws = []
        for _ in range(resamples):
            sampled_group_estimates = []
            for _group_index in names:
                selected_name = rng.choice(names)
                cases = grouped[selected_name]
                sampled_cases = [rng.choice(cases) for _case_index in cases]
                sampled_group_estimates.append(
                    (
                        statistics.fmean(item[0] for item in sampled_cases),
                        statistics.fmean(item[1] for item in sampled_cases),
                    )
                )
            control = statistics.fmean(item[0] for item in sampled_group_estimates)
            treatment = statistics.fmean(item[1] for item in sampled_group_estimates)
            draws.append((control, treatment, treatment - control))
        return draws

    @staticmethod
    def _ci(values: Sequence[float]) -> ConfidenceInterval:
        ordered = sorted(values)
        return ConfidenceInterval(
            low=ExperimentAnalyzer._quantile(ordered, 0.025),
            high=ExperimentAnalyzer._quantile(ordered, 0.975),
        )

    @staticmethod
    def _quantile(ordered: Sequence[float], probability: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * probability
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

    @staticmethod
    def _case_comparisons(
        cases: Mapping[UUID, Tuple[str, float, float]],
        threshold: float,
        *,
        invalid_case_ids: AbstractSet[UUID] = frozenset(),
    ) -> List[CaseComparison]:
        comparisons = []
        for case_id, (group, control, treatment) in sorted(
            cases.items(), key=lambda item: str(item[0])
        ):
            control_pass = control >= threshold
            treatment_pass = treatment >= threshold
            if case_id in invalid_case_ids:
                classification = "invalid"
            elif not control_pass and treatment_pass:
                classification = "win"
            elif control_pass and not treatment_pass:
                classification = "loss"
            elif control_pass:
                classification = "tie_positive"
            else:
                classification = "tie_negative"
            comparisons.append(
                CaseComparison(
                    case_id=case_id,
                    independence_group=group,
                    control_pass_rate=control,
                    treatment_pass_rate=treatment,
                    absolute_gain=treatment - control,
                    classification=classification,
                )
            )
        return comparisons

    @staticmethod
    def _wtl(cases: Sequence[CaseComparison]) -> WtlSummary:
        counts = {
            name: 0
            for name in ("win", "tie_positive", "tie_negative", "loss", "invalid")
        }
        for case in cases:
            counts[case.classification] += 1
        return WtlSummary(**counts)

    @staticmethod
    def _variant_summary(
        variant_id: UUID, datums: Iterable[_Datum]
    ) -> VariantRunSummary:
        selected = [datum for datum in datums if datum.run.variant_id == variant_id]
        costs = [
            datum.measurement.cost_microusd
            for datum in selected
            if datum.measurement is not None and datum.measurement.cost_microusd is not None
        ]
        all_costs_observed = len(costs) == len(selected)
        total_cost = sum(costs) if all_costs_observed else None
        pass_runs = sum(
            item.run.evaluation_outcome == EvaluationOutcome.PASS for item in selected
        )
        return VariantRunSummary(
            variant_id=variant_id,
            assigned_runs=len(selected),
            pass_runs=pass_runs,
            fail_runs=sum(
                item.run.evaluation_outcome == EvaluationOutcome.FAIL for item in selected
            ),
            invalid_runs=sum(not item.is_valid for item in selected),
            total_cost_microusd=total_cost,
            cost_per_success_microusd=(
                None if total_cost is None or pass_runs == 0 else total_cost / pass_runs
            ),
        )

    @staticmethod
    def _efficiency(
        unit: str,
        datums: Mapping[Tuple[UUID, UUID], _Datum],
        config: AnalysisConfig,
        extractor: ValueExtractor,
    ) -> EfficiencyComparison:
        control_values = []
        treatment_values = []
        paired: Dict[str, Dict[UUID, List[Tuple[float, float]]]] = {}
        block_ids = sorted({block_id for block_id, _variant_id in datums}, key=str)
        for block_id in block_ids:
            control_datum = datums[(block_id, config.control_variant_id)]
            treatment_datum = datums[(block_id, config.treatment_variant_id)]
            control = extractor(control_datum.measurement)
            treatment = extractor(treatment_datum.measurement)
            if control is not None:
                control_values.append(control)
            if treatment is not None:
                treatment_values.append(treatment)
            if control is not None and treatment is not None:
                paired.setdefault(control_datum.block.independence_group, {}).setdefault(
                    control_datum.block.case_id, []
                ).append((control, treatment))

        grouped_case_pairs: Dict[str, List[Tuple[float, float]]] = {}
        for group, cases in paired.items():
            grouped_case_pairs[group] = [
                (
                    statistics.fmean(item[0] for item in repeats),
                    statistics.fmean(item[1] for item in repeats),
                )
                for _case_id, repeats in sorted(cases.items(), key=lambda item: str(item[0]))
            ]
        control_mean: Optional[float]
        treatment_mean: Optional[float]
        paired_median_delta: Optional[float]
        if grouped_case_pairs:
            control_mean, treatment_mean, _gain = ExperimentAnalyzer._point_estimate(
                grouped_case_pairs
            )
            case_deltas = [
                treatment - control
                for cases in grouped_case_pairs.values()
                for control, treatment in cases
            ]
            paired_median_delta = statistics.median(case_deltas)
            draws = ExperimentAnalyzer._bootstrap_efficiency(
                grouped_case_pairs, config.bootstrap_resamples, config.bootstrap_seed
            )
            overhead_draws = [draw[0] for draw in draws if draw[0] is not None]
            median_draws = [draw[1] for draw in draws]
        else:
            control_mean = statistics.fmean(control_values) if control_values else None
            treatment_mean = statistics.fmean(treatment_values) if treatment_values else None
            paired_median_delta = None
            overhead_draws = []
            median_draws = []
        if control_mean is None or control_mean == 0 or treatment_mean is None:
            overhead = None
        else:
            overhead = (treatment_mean - control_mean) / control_mean
        return EfficiencyComparison(
            unit=unit,
            control_mean=control_mean,
            treatment_mean=treatment_mean,
            relative_overhead=overhead,
            relative_overhead_ci=(
                ExperimentAnalyzer._ci(overhead_draws) if overhead_draws else None
            ),
            paired_median_delta=paired_median_delta,
            paired_median_delta_ci=(
                ExperimentAnalyzer._ci(median_draws) if median_draws else None
            ),
            observed_control_runs=len(control_values),
            observed_treatment_runs=len(treatment_values),
            observed_pairs=sum(len(cases) for cases in grouped_case_pairs.values()),
        )

    @staticmethod
    def _bootstrap_efficiency(
        grouped: Mapping[str, Sequence[Tuple[float, float]]],
        resamples: int,
        seed: int,
    ) -> List[Tuple[Optional[float], float]]:
        rng = random.Random(seed + 1)
        names = sorted(grouped)
        draws: List[Tuple[Optional[float], float]] = []
        for _ in range(resamples):
            sampled_groups: List[Tuple[float, float]] = []
            sampled_deltas: List[float] = []
            for _group_index in names:
                cases = grouped[rng.choice(names)]
                sampled_cases = [rng.choice(cases) for _case_index in cases]
                control = statistics.fmean(item[0] for item in sampled_cases)
                treatment = statistics.fmean(item[1] for item in sampled_cases)
                sampled_groups.append((control, treatment))
                sampled_deltas.extend(item[1] - item[0] for item in sampled_cases)
            control = statistics.fmean(item[0] for item in sampled_groups)
            treatment = statistics.fmean(item[1] for item in sampled_groups)
            overhead = None if control == 0 else (treatment - control) / control
            draws.append((overhead, statistics.median(sampled_deltas)))
        return draws

    @staticmethod
    def _tokens(measurement: Optional[RunMeasurement]) -> Optional[float]:
        return None if measurement is None or measurement.total_tokens is None else float(
            measurement.total_tokens
        )

    @staticmethod
    def _latency(measurement: Optional[RunMeasurement]) -> Optional[float]:
        return None if measurement is None or measurement.duration_ms is None else float(
            measurement.duration_ms
        )

    @staticmethod
    def _cost(measurement: Optional[RunMeasurement]) -> Optional[float]:
        return None if measurement is None or measurement.cost_microusd is None else float(
            measurement.cost_microusd
        )
