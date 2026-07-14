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
- A unified multi-scenario facade for software engineering, MCP and Memory/RAG evaluations with
  frozen Skill hashes, explicit evidence classes, native metric preservation and immutable reports.
- A hash-pinned Process Scenario Agent boundary for MCP and Memory/RAG with baseline cleanliness,
  treatment Skill activation, oracle-free requests and hashed decision evidence.
- A backwards-compatible interactive Process Agent step loop for MCP and Memory/RAG with bounded
  Action/Observation history, deterministic budgets, observation-driven recovery and redacted
  per-step audit evidence.
- A leakage-safe Failure-Guided Skill Evolution controller that converts eligible train diagnoses
  into auditable hypotheses, reuses existing candidate search, applies an independent regression_dev
  gate and freezes a no-auto-publish handoff for Independent Final Evaluation.
- A hash/version-pinned Process Skill Proposal Generator with sanitized train-only requests, minimal
  environment inheritance, bounded JSON I/O, fail-closed validation and idempotent invocation evidence.
- A budget-gated Real Optimizer Evaluator that reuses paired observed-Agent execution, records
  candidate/Case outcomes and Trace references, and caches completed candidate/Case combinations.
- An expanded ten-case Python Bug Fix benchmark plan covering ten independent defect families and
  explicit train, validation_search, regression_dev and validation_confirm assignments.

### Evidence boundary

- The cross-repository evidence performs no model calls and proves only deterministic
  reconstruction and publication controls; it is not Agent-performance evidence.
- Unified MCP and Memory/RAG examples use precompiled deterministic plans; they validate the
  evaluation system but do not prove that a real Agent loaded or followed the example Skills.
- Process integration proves that a local Agent process received or did not receive a Skill and
  produced executable plans; deterministic tools and Fake Agents remain simulated evidence.
- Interactive integration additionally proves that subsequent Process decisions can consume
  deterministic environment observations; it remains simulated tool/Memory/RAG evidence.
- Failure-guided evolution currently uses deterministic hypotheses and simulated/Fake evaluators;
  it proves the optimization control loop, not that a real model-generated Skill is better.
- The Process Generator example is a deterministic local fixture and does not authorize Provider
  Secrets, paid calls or real-LLM optimization claims.
- Real optimizer Fake Process tests validate the observed execution chain with `simulated=false`;
  Provider-backed candidate-selection evidence requires a separately authorized smoke run.

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
