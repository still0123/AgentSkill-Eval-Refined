# Open-Source Design Log

Record external design research before code adoption. Local bug fixes derived only from this
repository are marked `local-only` and do not invent an external attribution.

| Date | Repository | License | Pattern reviewed | Adopted | Rejected / reason | Project adaptation | Verification |
|---|---|---|---|---|---|---|---|
| 2026-08-06 | local-only | N/A | Preserve infrastructure invalidity outside task W/T/L | Explicit final `INVALID` classification and decision | Treating invalid as failed task evidence; semantically wrong | Backward-compatible `invalid_count=0`; confirmation fails closed | 148 tests, Ruff, mypy, wheel clean-room Demo verification |
| 2026-08-07 | https://github.com/more-itertools/more-itertools | MIT | Pinned Git-history defects as benchmark sources | Before/after commits and regression tests | Synthetic rewrite; weaker provenance | Added mutation-fail and alternative-repair Oracle checks | 4 Cases, 48 offline Oracle commands, 18 observed-Agent matrix Runs |
| 2026-08-07 | https://github.com/tkem/cachetools | MIT | Pinned Git-history defects as benchmark sources | Before/after commits and regression tests | Synthetic rewrite; weaker provenance | Added mutation-fail and alternative-repair Oracle checks | 4 Cases, 48 offline Oracle commands, 18 observed-Agent matrix Runs |
| 2026-08-07 | https://github.com/mahmoud/boltons | BSD-3-Clause | Pinned Git-history defects as benchmark sources | Before/after commits and regression tests | Synthetic rewrite; weaker provenance | Added mutation-fail and alternative-repair Oracle checks | 4 Cases, 48 offline Oracle commands, 18 observed-Agent matrix Runs |
| 2026-08-07 | https://github.com/python-humanize/humanize | MIT | Pinned Git-history defects as benchmark sources | Before/after commits and regression tests | Synthetic rewrite; weaker provenance | Added mutation-fail and alternative-repair Oracle checks | 4 Cases, 48 offline Oracle commands, 18 observed-Agent matrix Runs |
| 2026-08-07 | https://github.com/dgilland/pydash | MIT | Pinned Git-history defects as benchmark sources | Before/after commits and regression tests | Synthetic rewrite; weaker provenance | Added mutation-fail and alternative-repair Oracle checks | 4 Cases, 48 offline Oracle commands, 18 observed-Agent matrix Runs |
| 2026-08-07 | https://github.com/Suor/funcy | BSD-3-Clause | Independent Git-history repository for expanded support | Four pinned defect families and regression tests | Reusing an exposed repository; would not broaden evidence | Published isolated DatasetVersion with four-way Oracle checks | 4 Cases, 48 offline Oracle commands, 24 observed-Agent matrix Runs |

Exact bundle SHA-256 values and per-Case before/after commit revisions are frozen in
`examples/benchmark-sources/optimization-split-v1/plan.yaml`,
`examples/benchmark-sources/optimization-split-v1/*.yaml`, and
`examples/benchmark-sources/regression-dev-v2/funcy-candidate-pool.yaml`.

Future entries must include an exact repository URL and revision when external code or design
material affects implementation.
