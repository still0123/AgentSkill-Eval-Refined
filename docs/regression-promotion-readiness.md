# Regression Promotion Readiness

`regression_dev` now answers two different questions instead of collapsing them into one boolean:

1. **No-regression gate**: did the winner avoid invalid evidence, excess losses, and excessive Token
   overhead?
2. **Promotion readiness gate**: did the winner also produce enough positive evidence to justify
   spending the independent confirmation and one-time locked-test budgets?

The distinction matters when both variants fail. A negative tie is valid evidence and does not show
a new regression, but it is not evidence that the candidate Skill is useful.

## Configuration

`SearchConstraintSpec` adds two thresholds:

```yaml
constraints:
  max_loss_cases: 0
  max_token_overhead_ratio: 0.50
  min_regression_positive_wins: 1
  min_regression_winner_pass_rate: 0.0
```

The production default requires at least one Case where the base fails and the winner passes. A
project may raise the minimum winner pass rate when the regression split is large enough. Setting
the positive-win threshold to zero is intended only for legacy orchestration fixtures and must not
be used as evidence that a Skill improved.

## Decisions

| Evidence | No regression | Promotion ready | Receipt | Handoff |
|---|---:|---:|---|---:|
| Invalid arm | no | no | `INVALID` | no |
| Excess loss or Token overhead | no | no | `REGRESSION_REJECTED` | no |
| All negative ties | yes | no | `PROMOTION_NOT_READY` | no |
| Thresholds met with positive wins | yes | yes | `COMPLETED` | yes |

The result records `positive_win_cases`, `negative_tie_cases`, `promotion_ready`, and stable blocker
codes. `inspect` and `verify` expose the readiness decision. Promotion workflow validation checks
readiness again, so a historical handoff created under the legacy no-regression-only policy cannot
be used to release a new Skill version without positive evidence.

## July 15 observed evidence interpretation

The original authorized max32 `regression_dev` receipt reported 16/16 Runs with no invalid evidence and cost
331,140 micro-USD. Across eight pairs it produced zero wins, eight negative ties, and zero losses;
both v1 and the validation-search winner had a 0% pass rate. Under the new policy:

```text
no_regression_passed = true
promotion_ready = false
blocker = INSUFFICIENT_POSITIVE_WINS
```

This historical interpretation was superseded by the 2026-07-15 offline grader re-audit. Fifteen
Runs had not executed their oracle because the selected Python interpreter lacked `pytest`; only one
Run contained a genuine task assertion failure. The correct classification is therefore 15
infrastructure `INVALID` plus one task `FAIL`, not eight valid negative ties. The immutable original
receipt is retained as defect evidence and must not be consumed by confirmation or promotion. The
correction is recorded in
`experiments/regression-dev-max32-deepseek-v4-pro-retry-2026-07-15/grader-environment-reaudit.sanitized.json`.

The remaining Agent-level failure still lacks normalized tool/file/command events. Therefore the
system abstains rather than inventing a planning, tool, or Skill-conflict explanation.

The repaired regression then produced 16/16 valid Runs, with both v1 and the winner passing all four
Cases: `0W/4T/0L`. It correctly remained `PROMOTION_NOT_READY`. More importantly, the observed base
had no failed Case, so this DatasetVersion could never produce the required positive win.

Freeze that feasibility decision before spending on another candidate:

```bash
agentskill-eval evolution execute opportunity-assess CONFIG EXECUTION_DIR \
  --output regression-opportunity.json
agentskill-eval evolution execute opportunity-verify regression-opportunity.json
```

The July 15 assessment is `INSUFFICIENT_OPPORTUNITY`: required positive wins `1`, maximum achievable
positive wins `0`, model calls `0`, Agent Runs `0`. When this evidence is bound through
`regression_opportunity_evidence_path`, runtime preflight refuses another paid evaluation on the
same provider/model, base Skill and DatasetVersion.

This evidence does not authorize confirmation or locked-test access. The next valid action is to
publish a new independent development DatasetVersion with observed base failures; it is not valid to
lower the threshold or consume hidden final-evaluation Cases.

## Funcy regression_dev v2 dataset (2026-07-15)

An independent four-Case DatasetVersion was published from the BSD-3-Clause `Suor/funcy`
repository. It is intentionally still marked `AWAITING_OBSERVED_BASELINE_SCREENING`: the offline
generator proves the before/after oracle and mutation gates, but it does not establish that the
current Skill v1 fails any Case. Baseline observation is the next experiment and must happen before
candidate search or locked evaluation.

See [the dataset record](./regression-dev-v2-dataset.md) for the fixed commits, tests, hashes and
claim boundary.
