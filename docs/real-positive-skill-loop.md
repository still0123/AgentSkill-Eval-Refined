# Real Positive Skill Loop Attempt

## Status

`STOPPED_AT_VALIDATION_SEARCH_NO_GAIN`

This bounded observed-Agent attempt did not produce a publishable Python Bug Fix Skill v2.
It stopped before regression, confirmation, and locked test, and did not start the Test Generation
Skill family.

## Frozen protocol

Five public Git-history cases were selected before real execution from independent repositories:

| Split | Repository | Case | Executed |
|---|---|---|---|
| train | more-itertools | `more-itertools-islice-release` | yes |
| validation_search | cachetools | `cachetools-cachedmethod-autospec` | yes |
| regression_dev | boltons | `boltons-lri-replacement` | no |
| validation_confirm | humanize | `humanize-intcomma-string-ndigits` | no |
| locked_test | pydash | `pydash-empty-intersperse` | no |

The release contains five DatasetVersions, five repository lineages, five independent defect
families, and 12 deterministic command-evidence records per Case. These cases are public and have
high contamination risk, so all conclusions are descriptive.

## Train evidence

The first attempt ended with two `INVALID` Runs because the configured credential returned HTTP
401. The environment was corrected without changing the Case, Skill, task, Runtime, or grading
protocol, then replayed once.

The replay produced two valid observed Runs:

- baseline: `FAIL`;
- Skill v1 treatment: `FAIL`;
- paired result: `TIE_NEGATIVE`;
- treatment Skill installed: `true`;
- deterministic pytest lifecycle assertion: failed;
- invalid Runs: `0`;
- observed cost: `48,432 microusd`.

The treatment Agent inspected production code but made no edit and did not execute the targeted
test before completion. FailureBridge accepted one real `VERIFICATION` finding and produced a
sanitized observed-train bundle. No repository name, Case ID, path, expected patch, or test answer
was exposed to the Proposal Generator.

## Proposal

The same frozen request hash was used for both calls. The first Provider response returned two
hypotheses but hit a local `HypothesisArtifact` minimum-length bug before persistence. After fixing
that compatibility defect, the request was replayed once.

The persisted Proposal contains two generic rules:

1. inspect repository test configuration and naming conventions before constructing the targeted
   verification command;
2. verify environment and dependency readiness before treating a test result as code evidence.

AI-assisted review found no Case ID, repository, path, patch, expected answer, or validation/locked
leakage. The successful call used 804 input tokens, 313 output tokens, and 291 microusd.

## Validation search

The search evaluated only Skill v1 and the two Proposal candidates on the frozen cachetools
validation Case. All Runs were observed real evidence, all Skill hashes were installed as expected,
all Secret scans were clean, and the deterministic Grader completed normally.

| Candidate | PASS rate | W/T/L vs v1 | Tokens | Latency ms | Cost microusd |
|---|---:|---:|---:|---:|---:|
| Skill v1 | 0.0 | reference | 106,302 | 33,423 | 47,051 |
| test-command rule | 0.0 | 0 / 1 / 0 | 122,809 | 20,878 | 53,867 |
| environment rule | 0.0 | 0 / 1 / 0 | 105,390 | 25,212 | 46,498 |

The legacy Pareto selector froze the environment candidate because it was cheaper and faster, even
though it had zero absolute gain. This attempt rejects that selection at the Goal gate. The search
constraint now requires an explicit minimum absolute gain, preventing future zero-gain publication.

## Budget ledger

| Resource | Used | Limit |
|---|---:|---:|
| Agent Runs | 8 | 16 |
| Proposal calls | 2 | 2 |
| Known observed cost | 207,471 microusd | 2,500,000 microusd |
| Conservative cost including failed persistence call | at most 210,259 microusd | 2,500,000 microusd |

No completed Run was replayed for additional cost.

## Stop decision

- regression_dev: not executed;
- validation_confirm: not executed;
- locked_test: not accessed;
- Promotion Gate: not entered;
- immutable SkillVersion v2: not published;
- Evidence Release: not produced;
- Test Generation Skill: not started.

The next attempt should change only the sanitized failure-evidence granularity. The Proposal input
must distinguish “deterministic verification failed” from the observed behavior “the Agent made no
edit and did not run the targeted test.” The Case selection, Runtime, grading protocol, and
confirmation/locked datasets should remain unchanged until a genuinely different general rule is
proposed under a fresh budget.

## Evidence

Full Trace, pytest output, Skill activation records, patches when present, and replayable audit
bundles remain under `.agentskill-eval/real-positive-loop/`. Sanitized summaries and bundle digests
are committed under `experiments/real-positive-skill-loop-2026-08-05/`.
