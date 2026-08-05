# Test Generation Runtime Fix and Corrected Replay

## Status

`COMPLETED_OBSERVED_NO_GAIN`

The Test Generation harness was repaired without changing the frozen Dataset, Cases, Skill, or
grader. A new four-Run observed-Agent smoke then completed with zero INVALID Runs.

The corrected paired result remains W/T/L `0 / 2 / 0`. This is now a valid no-gain result for the
exact frozen two-Case protocol because the treatment Skill handoff is directly observable.

## Runtime repair

The Process Agent now:

1. selects a Test Generation contract for `testgen-*` Cases;
2. requires `agent_regression_test.py` and prohibits production edits;
3. receives treatment-only, hash-bound Skill content through the isolated Agent HOME;
4. keeps the baseline HOME free of that Skill context;
5. honors one-based `read_file` offset and limit values;
6. retries one empty completion when the required artifact is missing;
7. records `task_mode`, `skill_context_loaded`, and `skill_context_sha256` in SessionResult.

No Proposal, Skill optimization, Dataset modification, grader modification, or new Case was
introduced.

## Deterministic verification

Four new regression tests failed before the Runtime repair and mapped directly to:

- Bug Fix/Test Generation instruction conflict;
- missing treatment Skill handoff;
- ignored focused-read range;
- empty completion accepted before artifact creation.

After the repair:

```text
Targeted tests: 13 passed
Ruff: passed
mypy: passed, 87 source files
Full pytest: 137 passed
```

## Frozen replay inputs

```text
Experiment ID: b97ec4eb-387b-5336-883c-108e99f58484
DatasetVersion ID: d90135df-d060-562a-9252-29322cc99847
Dataset SHA-256: eb27c384f0fed3fd136a0456bbcffc70829d8d1810c27952035bfbe2935f5390
Skill tree SHA-256: d1cfd04f321985620117316909ef12807a90f5740aa6161dcc6514f2e271b3f7
Skill content SHA-256: 4906a6fb2e02895b48afd3d9d8eacc1d30547b998fb4ab116363f1cc443babda
Agent SHA-256: 95f3352b6fa844e56d7215c114f14bfc286b25a0f6a1c738701b3e42166d479f
Runner SHA-256: b8473aad3fe997f3aa8de1e9bd9bc127e5254b25371567a0e07143afc809c359
```

The first invocation used a stale `OPENAI_API_KEY` and produced four zero-token infrastructure
INVALID Runs with HTTP 401. Those Runs are retained separately and excluded. The same frozen
configuration was replayed once with the previously verified `OPENAI_COMPATIBLE_API_KEY`.

## Corrected replay result

| Metric | without Skill | with Skill |
|---|---:|---:|
| PASS Runs | 0 / 2 | 0 / 2 |
| Total tokens | 134,235 | 78,835 |
| Latency ms | 91,020 | 86,787 |
| Cost microusd | 61,231 | 37,089 |
| Skill context loaded | 0 / 2 | 2 / 2 |

Aggregate:

```text
Completed Runs: 4
INVALID: 0
W/T/L: 0 / 2 / 0
Observed cost: 98,320 microusd
Authorized maximum: 250,000 microusd
```

Case behavior:

| Case | without Skill | with Skill | Classification |
|---|---|---|---|
| more-itertools | no generated file | no generated file | TIE_NEGATIVE |
| cachetools | write attempted, no gradeable file | file written and executed, but passed on buggy checkout | TIE_NEGATIVE |

For cachetools treatment, the generated test initially failed with `ModuleNotFoundError`, then ran
with `PYTHONPATH=src` and passed on the buggy checkout. The deterministic grader returned code 5:
the test did not expose the frozen defect. This is a test-quality failure, not infrastructure
failure.

## Interpretation

The Runtime confound identified in the original experiment is resolved:

- all four Runs used `task_mode=test_generation`;
- both treatment Runs loaded Skill content SHA-256
  `4906a6fb2e02895b48afd3d9d8eacc1d30547b998fb4ab116363f1cc443babda`;
- both baseline Runs recorded no Skill context;
- all Runs completed with deterministic grader outcomes.

The Skill still produced no independent WIN on these two Cases. The appropriate conclusion is:

> The corrected harness establishes a valid two-Case observed no-gain result. It does not support
> a population-level claim that the Test Generation Skill is generally ineffective.

No second Prompt revision, candidate search, automatic optimization, or Skill publication was
performed.

## Evidence

The local replay bundle is content-addressed:

```text
Replay bundle SHA-256:
da6730d4e9d39e8596646c42a4789e01d656f960bdaf5e3be6922fe9c1ef68cf
```

Full raw Trace, SessionResult, grader output, Skill activation records, and audit bundle remain in
the ignored `.agentskill-eval/test-generation-runtime-fix/evidence-v2/` workspace.
