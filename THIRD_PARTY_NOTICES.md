# Third-party notices

AgentSkill-Eval depends on third-party Python and JavaScript packages. Their copyright and license
terms remain with their respective authors and are recorded by `pyproject.toml`,
`apps/web/package.json` and `apps/web/package-lock.json`.

The repository also contains `examples/benchmark-sources/more-itertools.bundle`, an offline Git
bundle from [more-itertools](https://github.com/more-itertools/more-itertools), distributed by its
authors under the MIT License. It is retained as historical benchmark input evidence. Its pinned
commits, bundle hash and frozen upstream license hash are documented in
`examples/benchmark-sources/README.md` and in generated provenance manifests.

Generated DatasetVersions may contain material under licenses different from AgentSkill-Eval's
Apache-2.0 license. Consumers must inspect each candidate's frozen SPDX identifier, source URL,
commit and license hash before redistributing a dataset.
