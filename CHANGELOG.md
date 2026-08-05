# Changelog

All notable changes to this project are documented here. The project follows Semantic Versioning
after the first stable release.

## [Unreleased]

## [0.3.0] - 2026-08-06

### Added

- Completed the first observed-Agent Python Bug Fix v1 to v2 promotion loop with W/T/L `1/3/0`,
  immutable `python-bug-fix@2.0.0`, AI-assisted review, and a verified Evidence Release.
- Added the two-Case Python Test Generation family and retained its corrected observed no-gain
  result (`0/2/0`, zero INVALID) without automatic optimization or publication.
- Stage 3C budgeted real evolution execution with separately authorized `validation_search` and
  `regression_dev` stages, immutable receipts, idempotent replay, regression gating, and a
  confirmation-only handoff that keeps independent and locked data withheld.
- Stage 3B Real Evolution Dry-Run Orchestration, which binds the Stage 2 five-way release to the
  Stage 3A plan, integrity-checks only adaptive DatasetVersions, rehearses a hash-pinned local
  Process, and retains confirmation/locked inputs as path-free receipts.
- A proposal-only real LLM job and CLI that reuse the DeepSeek OpenAI-compatible Generator without
  constructing search, regression, confirmation, or locked-test work.
- Immutable proposal manifests and offline reports freezing provider/model parameters, prompt,
  schema, request, input and candidate hashes, token/cost evidence, and modification lineage.
- One authorized `deepseek-v4-pro` proposal-only smoke producing four candidates for 921 microusd,
  with offline verification and Secret scans passing and no search or locked-test access.

### Fixed

- Added a task-aware Test Generation Process Agent contract, focused file reads, required-artifact
  completion gate, and hash-bound Skill context delivery.
- Added fail-closed verification that Process Agent Skill context loaded state and SHA match the
  frozen baseline/treatment snapshots before formal evidence is generated.
- Disabled pytest's cache provider inside the offline verifier so test execution cannot mutate
  frozen fixtures and invalidate their audit hashes.

- Replaced the contradictory repository-per-split policy with one executable exposure-zone
  contract: repositories/forks cannot cross adaptive to holdout, while Case and defect-family
  identities remain unique across every split.
- Reallocated all twelve public Git-history Cases so `more-itertools` is adaptive-only,
  `cachetools` is holdout-only, and `locked_test` contains four one-shot public Cases.
- Added fail-closed split-plan audit and per-split generation CLI commands; historical Stage 3 runs
  that crossed the new boundary remain evidence but are ineligible for Promotion.

### Added

- Added Optimization Benchmark Split v1 with 20 real Git-history Cases from five independent
  repositories, four Cases per immutable train/search/regression/confirmation/locked DatasetVersion.
- Added strict repository/fork/patch-family isolation, a common frozen plan lineage, an
  optimizer-only view that withholds confirmation/locked inputs, and offline publish/verify/inspect
  CLI commands.

## [0.3.0-rc2] - 2026-08-04

### Fixed

- Bound Portfolio Demo summaries and Trace indexes to the verified replay bundle.
- Packaged the offline Dataset and Skill in the wheel and made same-workspace reruns idempotent.
- Added Dashboard schemas for the generated Demo evidence files.
- Removed unsupported MCP, Memory/RAG and Process Scenario public surfaces from the Refined build.
- Added tag-triggered release builds, checksums and build provenance.

## [0.3.0-rc1] - 2026-07-14

### Added

- A budget-gated Real Optimizer Evaluator that reuses paired observed-Agent execution, records
  candidate/Case outcomes and Trace references, and caches completed candidate/Case combinations.
- A twelve-case Python Bug Fix benchmark plan with independent defect families and explicit
  `train`, `validation_search`, `regression_dev` and `validation_confirm` assignments.
- A four-case immutable train DatasetVersion with offline verifier repetitions, alternative repairs
  and a sanitized publication record.
- A single-provider DeepSeek Skill Proposal Generator with explicit call/cost authorization,
  train-only sanitized inputs, structured candidate output, frozen prompt/schema hashes and
  idempotent no-cost replay.
- An Observed Failure Evidence Bridge that turns eligible real treatment failures into a traceable,
  train-only optimizer input without exposing secrets or locked-test data.
- SkillVersion Promotion Core and Promotion Workflow gates covering confirmation, locked test,
  human review, immutable parent lineage and explicit rejection states.
- Evolution Evidence Release CLI commands (`prepare`, `verify`, `inspect`) that generate an offline
  report, Skill diff, evidence index and tamper-evident audit bundle from promotion evidence.
- A project-completion evidence bundle, five-minute demo guide and interview/defence notes.

### Changed

- Python distribution version is now `0.3.0rc1`; the Dashboard package is `0.3.0-rc.1`.
- Tool-call-limit, loop and turn-limit exits retain their specific terminal reason instead of being
  collapsed into a generic execution error.

### Evidence boundary

- The two authorized Stage 3 train smokes did not produce eligible optimization evidence. The first
  had one pass and one pair invalid in both arms; the second had one pass and one hard Case ending in
  loop/turn limits. The DeepSeek proposal call was therefore not consumed.
- Stage 4 Promotion and Stage 5A.2 Evidence Release are validated with Fake/simulated fixtures. They
  prove workflow integrity and tamper detection, not that a real model-generated Skill v2 is better.
- Real Agent samples are descriptive evidence only; the small number of Cases does not support a
  generalized performance claim.
- MCP and Memory/RAG remain deterministic offline Labs. The Dashboard remains a local read-only UI.

## [0.2.0-rc1] - 2026-07-14

### Added

- Cross-repository Automatic Benchmark Generation with source provenance, independence groups,
  split-leakage guards and a deterministic multi-repository DatasetVersion.
- A unified multi-scenario facade for software engineering, MCP and Memory/RAG evaluation while
  preserving scenario-specific metrics and evidence classes.
- Hash-pinned Process Scenario Agent support and a bounded Action/Observation loop with redacted
  per-step evidence and deterministic budgets.
- Leakage-safe Failure-Guided Skill Evolution that converts eligible train diagnoses into auditable
  hypotheses, reuses candidate search and applies an independent regression gate.
- A hash/version-pinned Process Skill Proposal Generator with sanitized inputs, minimal environment
  inheritance, bounded JSON I/O and fail-closed validation.

### Evidence boundary

- Cross-repository reconstruction is deterministic benchmark evidence, not Agent-performance
  evidence.
- MCP, Memory/RAG, Process Agent and optimizer examples in this release use deterministic or Fake
  components and remain simulated evidence.

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

[Unreleased]: https://github.com/still0123/AgentSkill-Eval-Refined/compare/v0.3.0-rc.2...HEAD
[0.3.0-rc2]: https://github.com/still0123/AgentSkill-Eval-Refined/compare/v0.3.0-rc.1...v0.3.0-rc.2
[0.3.0-rc1]: https://github.com/ranmaoxia0123/AgentSkill-Eval/compare/v0.2.0-rc1...v0.3.0-rc1
[0.2.0-rc1]: https://github.com/ranmaoxia0123/AgentSkill-Eval/compare/v0.1.0-rc1...v0.2.0-rc1
[0.1.0-rc1]: https://github.com/ranmaoxia0123/AgentSkill-Eval/releases/tag/v0.1.0-rc1
