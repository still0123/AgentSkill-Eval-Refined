# Python Bug Fix v2 Expanded Generalization

## Status

`PRE_REGISTERED_RUNTIME_FROZEN`

No evaluation Case run has started. No result is claimed.

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

The new four-Case Funcy DatasetVersion passed the same offline checks:

```text
release SHA-256: 24ef413eb1d9e52d2a8896879bb5ad7902a4e3d3745b4f6a75cbedb41fb46509
DatasetVersion: cdb9f85b-d3a1-577f-b33d-8df2f5bafbbf
```

Generated DatasetVersion directories remain local because they contain reconstructed public
repository fixtures. Public evidence will contain hashes, metrics, and sanitized provenance only.

## Frozen runtime

Execution uses the local zero-metered-cost
`mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` model through MLX-LM. The model revision,
weights, tokenizer, server wrapper, process Agent, and `skill-up` binary are hash-bound in
[`protocol.yaml`](protocol.yaml).

A synthetic tool-use smoke passed. The full Runner smoke then completed two terminal Runs with
zero invalid observations and zero metered cost on excluded Case
`cachetools-cachedmethod-autospec`. Both arms failed the task (`TIE_NEGATIVE`), so this smoke
validates only the execution chain and contributes no efficacy evidence. Exact report and bundle
hashes are frozen in the protocol.

## Decision boundary

Passing supports only expanded descriptive evidence. It does not prove universal effectiveness:
the histories are public, Cases within a repository are correlated, and only Funcy is a completely
new repository relative to the original release.
