# Python Bug Fix v2 Expanded Generalization

## Status

`READY_FOR_RESTARTED_EVALUATION`

A previous partial matrix attempt was interrupted and its `/private/tmp` artifacts were removed by
external cleanup. No partial outcome is counted or claimed. The restarted attempt has not run any
evaluation Case.

## Question

Does immutable `python-bug-fix@2.0.0` improve valid paired outcomes beyond the four public Cases
used for its original Search-through-Locked release evidence?

## Frozen sample

The experiment evaluates 19 new v1/v2 Case pairs with three repeats per arm:

- 15 previously unexecuted Cases from the original frozen five-repository release;
- 4 independently reconstructed Funcy Cases from one new repository.

Five Cases already exposed to train, candidate selection, regression, confirmation, or locked
gates are excluded. Exact IDs and decision gates are frozen in
[`protocol.yaml`](protocol.yaml).

The original release evidence was `1 WIN / 3 TIE / 0 LOSS`. If all 19 pairs complete, the combined
descriptive sample contains 23 Cases across six repositories.

## Offline preparation

The five-way 20-Case release was reconstructed from pinned Git bundles and passed the existing
before-fail, after-pass, mutation-fail, and distinct-alternative-pass checks. Its release SHA-256 is:

```text
aa0b0ad1a38c8f6580cc0c962140565b5f4cba0db17441999df7e1e9cdf5b7ab
```

The new four-Case Funcy DatasetVersion passed the same offline checks under its required Python 3.9
oracle runtime:

```text
DatasetVersion: cdb9f85b-d3a1-577f-b33d-8df2f5bafbbf
Dataset content SHA-256: 2590b7716a63ec93d8bdea6b4e6538bc48f8083e33646c39bcd14b196203c085
```

The previous Funcy release wrapper hash could not be reproduced after artifact loss. Protocol
revision `v1alpha2` therefore records both that negative provenance result and the newly rebuilt
release, instead of silently reusing the old hash.

Generated DatasetVersion directories remain local because they contain reconstructed public
repository fixtures. Public evidence will contain hashes, metrics, and sanitized provenance only.

## Frozen runtime

Execution uses the local zero-metered-cost
`mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` model through MLX-LM. The model revision,
weights, tokenizer, server wrapper, process Agent, and `skill-up` binary are hash-bound in
[`protocol.yaml`](protocol.yaml).

A synthetic tool-use smoke passed. The restarted full Runner smoke completed two terminal Runs
with zero invalid observations on excluded Case `cachetools-cachedmethod-autospec`; both arms
failed (`TIE_NEGATIVE`). Exact hashes are frozen in the protocol. Neither smoke contributes
efficacy evidence.

The committed [`run_matrix.py`](run_matrix.py) driver executes the six DatasetVersions in frozen
order. Its SHA-256 is part of the protocol.

## Decision boundary

Passing supports only expanded descriptive evidence. It does not prove universal effectiveness:
the histories are public, Cases within a repository are correlated, and only Funcy is a completely
new repository relative to the original release.
