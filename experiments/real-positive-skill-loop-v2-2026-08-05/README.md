# Real Positive Skill Loop v2 Evidence

This directory is the sanitized public evidence index for the second bounded observed-Agent
experiment.

## Outcome

- validation_search: one independent WIN;
- regression_dev: no LOSS or INVALID;
- validation_confirm: no LOSS or INVALID;
- locked_test: no LOSS or INVALID;
- aggregate Search through Locked W/T/L: `1 / 3 / 0`;
- immutable SkillVersion: `python-bug-fix@2.0.0`;
- review identity: `AI-assisted review (OpenAI Codex)`;
- verified Evidence Release SHA-256:
  `04ea593b840bb68c24d87381cadea416872f58bf67e95d565aa832d0a36706f2`;
- claim class: descriptive observed evidence for frozen public Cases only.

The published Skill adds one generic post-fix verification rule. It contains no Case ID,
repository, path, patch, or expected answer.

## Test Generation

The minimal two-Case Test Generation family completed a valid without/with-Skill paired evaluation.
Its final result was `0 / 2 / 0` with zero INVALID Runs. Both variants failed the before-fail /
after-pass Oracle, so this is retained as a negative result and was not optimized or published.

The preceding four-Run attempt is retained separately and excluded as environment-error evidence:
a missing grader shebang caused execution as shell. Its raw Runner outcome was `FAIL`, not
`INVALID`; both fields are preserved. Only that common grader entry point changed for replay.

Full Trace, pytest/grader output, Skill activation, patches, and replay bundles remain in the
ignored `.agentskill-eval/real-positive-loop-2/` workspace. `audit-bundle-digests.json` binds this
public summary to those retained artifacts.
