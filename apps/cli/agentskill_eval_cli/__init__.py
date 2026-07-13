"""Command-line entry point for AgentSkill-Eval."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentskill-eval")
except PackageNotFoundError:  # pragma: no cover - only used from an unpackaged source tree
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
