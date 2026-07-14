# Stage 3C: Budgeted Real Search / Regression Execution

Stage 3C turns the frozen Stage 3A plan and verified Stage 3B dry run into two separately
authorized observed-Agent stages:

```text
verified plan + verified dry run + real proposals
        ↓ explicit authorization 1
validation_search / successive halving
        ↓ immutable winner receipt
        ↓ explicit authorization 2
regression_dev / base-versus-winner gate
        ↓
confirmation-handoff.json
```

It does not open `validation_confirm` or `locked_test`, run an independent final evaluation, or
publish Skill v2. Those operations remain separate approval boundaries.

## Inputs

Copy
[`real-evolution-runtime.example.yaml`](../examples/optimizer/evolution-plan/real-evolution-runtime.example.yaml)
and bind it to:

- one verified Stage 3A execution plan;
- its matching Stage 3B dry run;
- the same real-LLM proposal job and base Skill;
- the published Stage 2 benchmark workspace;
- one observed-Agent configuration whose file hash matches the plan;
- a manually authored comparator Skill.

The runtime recomputes the proposal, Skill, Agent configuration and DatasetVersion hashes. The
runtime search parameters must reproduce the exact candidate-case count frozen by Stage 3A.

## Commands and paid boundaries

Preflight performs no Agent call:

```bash
agentskill-eval evolution execute preflight CONFIG
```

It prints the exact provider, model, planned Run count and cost ceiling for each adaptive stage.
Search requires its own explicit authorization:

```bash
agentskill-eval evolution execute search CONFIG \
  --workspace .agentskill-eval/evolution \
  --confirm-real-run \
  --max-agent-runs SEARCH_RUNS \
  --max-cost-microusd SEARCH_COST
```

Regression is never started by the search command and requires a second authorization:

```bash
agentskill-eval evolution execute regression CONFIG \
  --workspace .agentskill-eval/evolution \
  --confirm-real-run \
  --max-agent-runs REGRESSION_RUNS \
  --max-cost-microusd REGRESSION_COST
```

An authorization below the planned envelope or above the frozen stage cap is rejected. No command
falls back to a simulated evaluator.

Inspect and verify a checkpoint:

```bash
agentskill-eval evolution execute inspect EXECUTION_DIR
agentskill-eval evolution execute verify EXECUTION_DIR
```

## Evidence and replay

After search, the execution directory contains an immutable search result, receipt, preflight and
manifest. Repeating the same command returns the verified receipt instead of creating new Agent
Runs. After a passing regression gate it additionally contains:

```text
real-evolution-executions/EXECUTION_ID/
├── runtime-preflight.json
├── validation-search-result.json
├── validation-search-receipt.json
├── regression-dev-result.json
├── regression-dev-receipt.json
├── confirmation-handoff.json
├── adaptive-execution-report.html
└── runtime-manifest.json
```

Every artifact is hashed by `runtime-manifest.json`; modifying a result or receipt makes `verify`
fail. The handoff remains `AWAITING_INDEPENDENT_FINAL_EVALUATION` and records
`locked_test_accessed=false`.

If search produces no eligible candidate, Stage 3C freezes a `NO_WINNER` receipt and does not allow
regression to start. If the winner fails regression, it freezes `REGRESSION_REJECTED` evidence and
does not create a confirmation handoff. Negative outcomes are retained rather than retried with a
changed split or silently omitted.

## Claim boundary

Completing Stage 3C proves only that a real proposal winner survived adaptive search and the
configured development regression gate. It does not establish independent confirmation,
locked-test performance, population-level generalization, or publishability. A `NO_WINNER` or
failed regression is a valid experimental result and must not be bypassed by changing the split.
