# Cross-repository Benchmark evidence (2026-07-14)

This directory records a sanitized, fully offline publication run of the
Automatic Benchmark Generation MVP. No Agent, model API, credential, network
request, or paid inference was used.

## Scope

- 2 MIT-licensed open-source repositories
- 4 independent historical defect families
- 4 candidates published to `validation_search`
- 48 deterministic verifier executions: before, after, mutation, and
  alternative repair, each repeated three times per candidate
- 10 passed quality gates per candidate
- Human review represented by the explicit local audit actor

Every before fixture comes from its pinned pre-fix commit. The same regression
test fails three times before the fix, passes three times after the fix, fails
three times after reversing the production patch, and passes three times with a
distinct alternative repair.

## Replay

Follow `docs/automatic-benchmark-generation.md` using:

- `examples/benchmark-sources/more-itertools.bundle`
- `examples/benchmark-sources/cachetools.bundle`
- `examples/benchmark-sources/cross-repository-generation.example.yaml`

The spec hash is path-independent, so cloning the bundles into another absolute
directory preserves the job identity. Timestamps in transition manifests are
expected to differ; frozen source, candidate, artifact, and DatasetVersion
identities do not.

`result.sanitized.json` contains only hashes, public repository provenance, and
aggregate verification facts. Raw temporary workspaces and verifier output are
not committed.

## Claim limit

This proves deterministic multi-repository reconstruction, verification,
deduplication, human-gated publication, immutable metadata coverage, and split
leakage prevention for four public Python defect histories. It is not evidence
of Agent performance and does not establish population-level Benchmark quality.
