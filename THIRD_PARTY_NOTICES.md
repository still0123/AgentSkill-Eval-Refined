# Third-party notices

AgentSkill-Eval depends on third-party Python and JavaScript packages. Their copyright and license
terms remain with their respective authors and are recorded by `pyproject.toml`,
`apps/web/package.json` and `apps/web/package-lock.json`.

The repository also contains offline Git bundles from
[more-itertools](https://github.com/more-itertools/more-itertools) (MIT),
[cachetools](https://github.com/tkem/cachetools) (MIT),
[boltons](https://github.com/mahmoud/boltons) (BSD-3-Clause),
[humanize](https://github.com/python-humanize/humanize) (MIT), and
[pydash](https://github.com/dgilland/pydash) (MIT). They are retained as historical benchmark input
evidence. Pinned commits, bundle hashes and frozen upstream license hashes are documented in
`examples/benchmark-sources/README.md` and in generated provenance manifests.

Generated DatasetVersions may contain material under licenses different from AgentSkill-Eval's
Apache-2.0 license. Consumers must inspect each candidate's frozen SPDX identifier, source URL,
commit and license hash before redistributing a dataset.
