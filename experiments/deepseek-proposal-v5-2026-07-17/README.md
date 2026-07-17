# DeepSeek Proposal v5

This experiment records one authorized DeepSeek proposal-generation call. The
request contained only the frozen Python Bug Fix Skill and the sanitized
observed `train` failure bundle. No validation, regression, confirmation,
locked-test, or Agent smoke was executed.

DeepSeek returned four structured hypotheses:

- `inspect-tool-schema-before-edit`
- `verify-edit-outcome`
- `use-exact-tool-argument-names`
- `recover-from-failed-edit`

The output is a set of unvalidated hypotheses. The next stage must evaluate
these candidates against the deterministic Bug Fix oracle with DeepSeek as the
real Agent executor.
