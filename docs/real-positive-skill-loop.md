# Real Positive Skill Loop

## Status

`COMPLETED_OBSERVED_CANDIDATE_LOOP`

The first bounded attempt stopped at validation_search with zero gain. After a fail-closed
FailureBridge recovery fix, one separately authorized Proposal/Search round completed the full
observed-Agent candidate loop and published historical immutable `python-bug-fix@2.0.0`. Under the
current release policy this artifact is non-regression evidence, not a verified-improvement claim,
because the independent confirmation and locked stages contained no WIN.

## Attempt 1: retained negative result

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
must distinguish “deterministic verification failed” from the observed behavior “the structured
session recorded only read-only inspection actions; no edit action or test-oriented command was
observed.” The Case selection, Runtime, grading protocol, and confirmation/locked datasets should
remain unchanged until a genuinely different general rule is proposed under a fresh budget.

## Post-attempt recovery fix

After the paid experiment stopped, FailureBridge was updated to derive that case-agnostic behavior
summary from the hash-bound `session-result.json`. It verifies the artifact manifest and declared
tool-call count, recognizes only a strict allowlist of read-only inspection commands, ignores
`workspace_diff` because the Runner may capture pre-existing host-worktree changes, and abstains on
unknown commands or schema drift. The generated Proposal request contains only the fixed summary,
never tool arguments, commands, paths, repository names, patches, or test output.

This recovery fix was verified by rebuilding a new FailureBundle from the retained train replay
without an Agent or Proposal call. It does not alter the recorded Search result, consume additional
budget, or establish a Skill v2.

## Attempt 2: positive loop

The second attempt reused the exact frozen train/search/regression/confirmation/locked Cases,
Process Agent, DeepSeek Runtime, Grader, and statistics. Only the sanitized FailureBundle and
Proposal request changed.

The enriched failure evidence stated that the structured session contained only read-only
inspection actions and no observed edit or test command. One authorized Proposal call generated
two generic candidates:

1. mandate reproduction before editing;
2. require post-fix verification and continued iteration after a failed check.

AI-assisted review found no Case ID, repository, code path, patch, expected answer, or holdout
leakage. The call used 827 input tokens, 287 output tokens, and 610 microusd.

### Stage results

| Stage | v1 | v2 | W/T/L | v1 tokens | v2 tokens | v1 cost | v2 cost |
|---|---|---|---:|---:|---:|---:|---:|
| validation_search | FAIL | PASS | 1 / 0 / 0 | 106,302 | 114,252 | 47,051 | 51,167 |
| regression_dev | FAIL | FAIL | 0 / 1 / 0 | 103,562 | 110,538 | 45,527 | 48,924 |
| validation_confirm | FAIL | FAIL | 0 / 1 / 0 | 105,504 | 97,992 | 46,674 | 43,654 |
| locked_test | FAIL | FAIL | 0 / 1 / 0 | 92,948 | 93,741 | 41,243 | 41,375 |
| **Total** |  |  | **1 / 3 / 0** | **408,316** | **416,523** | **180,495** | **185,120** |

All stages used observed real evidence. Search produced the required independent WIN.
Regression, confirmation, and locked test contained no LOSS or INVALID. Confirmation and locked
were no-loss `TIE_NEGATIVE` checks; they do not constitute additional improvement claims.

The second attempt consumed:

- 1 Proposal call;
- 9 new Agent Runs;
- 378,446 microusd including both Proposal candidates and all gates.

The historical promotion workflow `0353ca42-f58c-52d9-afd4-7589c49db1d0` passed under the former
no-loss gate. Review was recorded as
`AI-assisted review (OpenAI Codex)`, not human review. Immutable SkillVersion
`a434afe8-cc6b-5d80-a4af-cd6819d53e64` was published with content SHA-256
`f14cd4a975b8a1820971e824b5f82ab9b79dee071a13acb673e6b2019720e13c`.

Skill v2 adds only this general guidance:

> After making a change, always run the reproduction command again and confirm it passes. If it
> fails, iterate on the fix before proceeding.

## Test Generation family

After Skill v2 publication, a minimal Python Test Generation family was frozen from two independent
public Git histories. The Agent could only create `agent_regression_test.py`; the deterministic
grader rejected production edits and required the generated test to fail on before production and
pass after replacing only the frozen production files.

The first four Runs were retained and excluded at the research layer as `ENVIRONMENT_ERROR`
evidence because a missing grader shebang caused execution as shell. The Runner's raw outcome was
`FAIL` rather than `INVALID`, so both values remain explicit in the sanitized report. After fixing
only that grader entry point, the same Cases, tasks, Skill, Runtime, and metrics were replayed once.

Final Runner-valid result:

| Metric | without Skill | with Skill |
|---|---:|---:|
| PASS rate | 0.0 | 0.0 |
| Tokens | 128,311 | 143,771 |
| Latency ms | 51,133 | 43,351 |
| Cost microusd | 57,048 | 63,649 |

- W/T/L: `0 / 2 / 0`;
- invalid Runs: `0`;
- observed cost: `120,697 microusd`;
- conclusion: four valid task-level FAIL outcomes, but a confounded Skill comparison;
- no candidate search, optimization, or version publication was performed.

The Dataset builder's hidden reference oracle independently proved before-fail/after-pass three
times per Case before any Agent run.

Post-hoc Trace diagnosis found that the frozen Process Agent was specialized for production-code
Bug Fix tasks. Its higher-priority system prompt required a source edit, prohibited creating a
temporary reproduction test, and emitted a mandatory `replace_in_file` nudge. The Custom Engine
SessionInput also contained no Skill content, and baseline/treatment model messages were identical
for each Case. The treatment therefore proved Skill installation but not Skill delivery or
activation at model-input level.

The `0 / 2 / 0` result remains an honest record of observed execution, but it is not valid negative
evidence about Test Generation Skill efficacy. See
[Test Generation Negative-Result Diagnosis](./test-generation-negative-result-diagnosis.md).

The Runtime was subsequently repaired without changing the Dataset, Cases, Skill, or grader. A
new four-Run smoke proved treatment-only Skill context delivery and baseline cleanliness, completed
with 0 INVALID, and still produced W/T/L `0 / 2 / 0`. This corrected result is valid no-gain
evidence for the exact two frozen Cases, not a general Skill-efficacy claim. See
[Test Generation Runtime Fix and Corrected Replay](./test-generation-runtime-fix.md).

## Evidence

Full Trace, pytest output, Skill activation records, patches when present, and replayable audit
bundles remain under `.agentskill-eval/real-positive-loop/` and
`.agentskill-eval/real-positive-loop-2/`. Sanitized summaries and bundle digests are committed under
`experiments/real-positive-skill-loop-2026-08-05/` and
`experiments/real-positive-skill-loop-v2-2026-08-05/`.
