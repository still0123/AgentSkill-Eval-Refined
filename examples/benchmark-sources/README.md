# Audited offline benchmark source

`more-itertools.bundle` is an offline Git bundle of the public
[`more-itertools`](https://github.com/more-itertools/more-itertools) repository. The upstream
project is MIT-licensed. The bundle preserves the exact historical commits used by the two
MVP candidates; it is input evidence, not a generated toy repository.

- Bundle SHA-256: `32be67f33e17d8390d452090fcb5e59ab9a9e1238b5ddbc6c1deffdd002c4cca`
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

Pinned defects:

- `cca32949f12d473fd823e37a5530c30d2faa1332`, parent
  `c834d6e4a0c4280b7b7750cb0de8dd8acb3d4c2c`: `last()` with a disabled
  `__reversed__` implementation.
- `ae37eb38a1d3958d764ff3ec43107116dfe29135`, parent
  `f36c88fe03688fa442154ef14f429bcfa4c38525`: strict counted sampling with an
  undersized population.

The generator overlays the after-commit regression tests onto each before-commit fixture. It
then runs before, after, reverse-patch mutation, and an independently written alternative fix
three times each under a controlled environment.
