# Audited offline benchmark source

`more-itertools.bundle` is an offline Git bundle of the public
[`more-itertools`](https://github.com/more-itertools/more-itertools) repository. The upstream
project is MIT-licensed. The bundle preserves the exact historical commits used by the initial two
MVP candidates and the later adaptive split expansion; it is input evidence, not a generated toy
repository.

- Bundle SHA-256: `9e19644b0027cc11502b5ccc959abce2847b8cf94a3446b09d45a1cd716651bb`
- Frozen upstream `LICENSE` SHA-256:
  `09f1c8c9e941af3e584d59641ea9b87d83c0cb0fd007eb5ef391a7e2643c1a46`

Prepare the source without network access:

```bash
git clone examples/benchmark-sources/more-itertools.bundle \
  .agentskill-eval/sources/more-itertools
git -C .agentskill-eval/sources/more-itertools remote set-url origin \
  https://github.com/more-itertools/more-itertools.git
```

Then copy `more-itertools-generation.example.yaml`, replace `repository_path` with the absolute
clone path, and run `agentskill-eval benchmark generate`.

Initial pinned defects:

- `cca32949f12d473fd823e37a5530c30d2faa1332`, parent
  `c834d6e4a0c4280b7b7750cb0de8dd8acb3d4c2c`: `last()` with a disabled
  `__reversed__` implementation.
- `ae37eb38a1d3958d764ff3ec43107116dfe29135`, parent
  `f36c88fe03688fa442154ef14f429bcfa4c38525`: strict counted sampling with an
  undersized population.

The generator overlays the after-commit regression tests onto each before-commit fixture. It
then runs before, after, reverse-patch mutation, and an independently written alternative fix
three times each under a controlled environment.

`cachetools.bundle` is the second offline source. It freezes the MIT-licensed
[`cachetools`](https://github.com/tkem/cachetools) history used by the cache-related candidates.

- Bundle SHA-256: `9933f9067dbc4da476cdc2612625422251916d53d8945153b0795dca1371258c`
- Initial pinned defects: cachedmethod class-level autospec (`57d2e481...`) and hash-key pickle
  restoration (`748d10de...`).

Use `cross-repository-generation.example.yaml` to reconstruct twelve candidates from twelve independent
defect families and publish them into one immutable DatasetVersion. The v1alpha2 spec excludes
machine-local clone paths from the semantic job hash, uses explicit provenance families as
independence groups, and blocks a fork lineage from crossing the adaptive/holdout exposure boundary
in the same workspace.

`real-bug-fix-split-plan.yaml` assigns every expanded candidate exactly once to `train`,
`validation_search`, `regression_dev`, `validation_confirm`, or `locked_test`. The adaptive zone
(`train`, search and regression) uses only `more-itertools`; the frozen holdout zone (confirmation
and locked test) uses only `cachetools`. Case, patch-family and independence identities remain unique
across every split. The locked cases are public and high-contamination, so they prove one-shot
workflow behavior rather than hidden-benchmark generalization.

Audit the complete allocation before generating any split:

```bash
agentskill-eval benchmark audit-split-plan \
  examples/benchmark-sources/real-bug-fix-split-plan.yaml
```

Generate one split from the audited plan after replacing source paths with local offline clones:

```bash
agentskill-eval benchmark generate-split \
  examples/benchmark-sources/real-bug-fix-split-plan.yaml locked_test \
  --workspace .agentskill-eval/benchmark
```
