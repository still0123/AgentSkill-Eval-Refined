# Stage 3 train Benchmark expansion evidence

This directory records a sanitized, fully offline publication run that expands
the frozen Python Bug Fix `train` split from two to four independent historical
defect families. No Agent, model API, credential, network request, or paid
inference was used.

## Scope

- 2 MIT-licensed public repositories
- 4 independent `train` defect families
- 2 newly reconstructed compact cases:
  - `more-itertools-split-after-empty-tail`
  - `cachetools-missing-oversized-value`
- 48 deterministic verifier executions across all four train candidates
- before, after, mutation, and alternative repair each repeated three times
- all ten quality gates passed and all four candidates were explicitly reviewed
- immutable DatasetVersion content SHA-256:
  `e1abef442a625adb6640f72f395de12ab0c63bfac7684755db1595ef657beb7b`

For every candidate, the test fails three times on the pinned pre-fix fixture,
passes three times after the historical fix, fails three times after mutation,
and passes three times with a distinct alternative repair. The published
DatasetVersion contains frozen case, fixture, grader, provenance, and metadata
hashes.

## Replay

Follow `docs/automatic-benchmark-generation.md` using the two offline bundles,
`examples/benchmark-sources/cross-repository-generation.example.yaml`, and the
`train` assignments in
`examples/benchmark-sources/real-bug-fix-split-plan.yaml`.

`result.sanitized.json` contains public provenance, immutable hashes, quality
gate outcomes, and aggregate verifier exits. Raw fixtures, command output, and
machine-specific paths are intentionally not committed.

## Claim limit

This proves deterministic reconstruction and publication of four independent
real-history train cases. It does not contain Agent-performance evidence and
does not show that a Skill is better. The two added cases are intended to make
the next bounded train smoke more likely to yield an eligible, completed task
failure without weakening the failure-evidence gate.
