# Test Generation Runtime Fix Evidence

This directory records the sanitized result of the corrected Test Generation harness smoke.

## Result

- Experiment: `b97ec4eb-387b-5336-883c-108e99f58484`
- DatasetVersion: `d90135df-d060-562a-9252-29322cc99847`
- Cases: 2
- observed Runs: 4
- INVALID: 0
- W/T/L: `0 / 2 / 0`
- observed cost: `98,320 microusd`
- outcome: valid no-gain evidence for the exact frozen protocol

Both treatment Runs loaded the frozen Test Generation Skill content hash. Both baseline Runs
recorded no Skill context. The treatment generated and ran a cachetools test, but that test passed
on the buggy checkout and therefore failed the before-fail/after-pass grader.

No Proposal, optimization, new Case, or Skill publication followed this result.

## Excluded credential failure

The first invocation used a stale `OPENAI_API_KEY` and produced four zero-token HTTP 401
infrastructure INVALID Runs. It is retained in the ignored workspace and excluded from Skill
evidence. The corrected replay changed only the credential source.

## Evidence binding

```text
Agent SHA-256:
95f3352b6fa844e56d7215c114f14bfc286b25a0f6a1c738701b3e42166d479f

Replay bundle SHA-256:
da6730d4e9d39e8596646c42a4789e01d656f960bdaf5e3be6922fe9c1ef68cf
```

See `docs/test-generation-runtime-fix.md` for the evidence-based interpretation.
