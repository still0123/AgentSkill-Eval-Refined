"""Ensure all planned Python package boundaries remain importable."""

import importlib

PACKAGE_NAMES = (
    "agentskill_eval_benchmark_gen",
    "agentskill_eval_cli",
    "agentskill_eval_contracts",
    "agentskill_eval_experiment",
    
    "agentskill_eval_real_evidence",
    "agentskill_eval_runner_adapters",
    "agentskill_eval_skill_optimizer",
    "agentskill_eval_trace_intelligence",
    "agentskill_eval_worker",
)


def test_planned_packages_are_importable() -> None:
    for package_name in PACKAGE_NAMES:
        assert importlib.import_module(package_name) is not None
