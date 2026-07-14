# Optimization Benchmark Split v1 offline evidence

This directory records the sanitized result of publishing five immutable DatasetVersions for
Skill v1→v2 optimization. The run was fully offline: no Agent, model API, credential, network
request, or paid inference was used.

## Result

- 20 real Git-history bug Cases from 5 independent Python repositories;
- 4 Cases in each of `train`, `validation_search`, `regression_dev`, `validation_confirm`, and
  `locked_test`;
- 20 independent patch families / independence groups;
- before-fail, after-pass, mutation-fail, and alternative-pass each repeated 3 times;
- 48 command evidence records per split, 240 total;
- all candidates passed deterministic quality gates and explicit review;
- release SHA-256:
  `aa0b0ad1a38c8f6580cc0c962140565b5f4cba0db17441999df7e1e9cdf5b7ab`;
- model calls: 0; paid cost: 0.

`result.sanitized.json` records release, DatasetVersion and public provenance hashes. Raw fixtures,
command output and machine-specific paths are intentionally not committed; they are reproduced from
the hash-pinned bundles and plan under `examples/benchmark-sources/optimization-split-v1/`.

## Isolation

Each repository and fork lineage belongs to exactly one split. The optimizer view exposes only
train/search/regression DatasetVersion paths. Confirmation and locked inputs are replaced by receipt
hashes, and the locked access flag remains false.

The source histories are public and therefore have high pretraining-contamination risk. “Locked”
means one-shot workflow isolation, not secret benchmark material. This evidence proves offline data
reconstruction and isolation; it is not Agent-performance evidence and does not prove Skill gain.
