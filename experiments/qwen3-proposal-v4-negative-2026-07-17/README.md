# Qwen3 + Proposal v4 negative evidence

This is the sanitized record of the latest real-agent attempt to evaluate the
four DeepSeek Proposal v4 candidates. It is retained as a negative result and
must not be treated as confirmation or a Skill v2 release.

The validation search consumed 40 local Agent Runs at zero provider cost. The
winner was `inspect-tool-schema-before-edit`, but its validation result was not
better than the baseline: both were 2/4 on the four-case development split.

The independent regression retry used the same frozen winner and dataset. It
consumed 8 local Agent Runs at zero provider cost. Three winner cases produced
valid failures and `boltons-lri-replacement` returned a provider HTTP 400 and
was classified as `invalid`. The corrected regression gate therefore returned
`REGRESSION_REJECTED`; no confirmation handoff was created.

This result supports only the following claims:

- the real Agent → trace → evaluation → invalid-evidence gate is executable;
- Proposal v4 did not demonstrate an improvement on this split;
- the Qwen/DeepSeek execution combination was not stable enough for a positive
  regression claim on `boltons-lri-replacement`.

It does not support a population-level Skill claim, confirmation evidence,
locked-test evidence, or Skill v2 publication.

The next experiment changes the real Agent execution provider to DeepSeek while
keeping the evaluator, dataset, Skill, runner, and deterministic grading
protocol auditable and fixed. It begins with a separately authorized four-Run
smoke.
