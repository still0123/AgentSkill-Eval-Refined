# Changelog

All notable changes to this project are documented here. The project follows Semantic Versioning
after the first stable release.

## [Unreleased]

### Added

- GitHub Actions quality gates for Python, Dashboard, wheel packaging, schemas and Secret scanning.
- Release, contribution and security policy metadata.
- Cross-repository Automatic Benchmark Generation v1alpha2 with explicit source keys,
  provenance-family independence groups and published split-leakage guards.
- A second audited offline source (`cachetools`) and a four-case, two-repository deterministic
  evidence bundle whose DatasetVersion hash covers case, fixture, grader, provenance and metadata.

### Evidence boundary

- The cross-repository evidence performs no model calls and proves only deterministic
  reconstruction and publication controls; it is not Agent-performance evidence.

## [0.1.0-rc1] - 2026-07-13

### Added

- Auditable paired experiments with immutable manifests, recovery, replay bundles and reports.
- Trace Intelligence, deterministic failure diagnosis and paired trace comparison.
- Automatic Benchmark Generation, Benchmark-guided Skill Search and Independent Final Evaluation.
- Real Agent preflight, confirmation, budget, cancellation and evidence boundaries.
- Deterministic offline MCP and Memory/RAG evaluation Labs.
- Vue 3 read-only evaluation Dashboard.

### Evidence boundary

- Offline demos and Labs remain explicitly simulated and do not support real-agent performance claims.
- RC1 includes real-agent execution infrastructure and Fake Process Agent CI coverage; a complete,
  successful paid smoke/evidence report is a separate release-evidence artifact.

[Unreleased]: https://github.com/ranmaoxia0123/AgentSkill-Eval/compare/v0.1.0-rc1...HEAD
[0.1.0-rc1]: https://github.com/ranmaoxia0123/AgentSkill-Eval/releases/tag/v0.1.0-rc1
