# Real Validation Search Resume

Optimization v2 persists a non-secret session ledger in the supplied workspace:

```text
optimization-v2-session.json
optimization-v2-screening.json
optimization-v2-screening.html
```

The ledger gives every baseline/candidate/case arm a deterministic `run_key`. A completed
candidate and its completed baseline evidence are never invoked again. On a later process,
the runner reconstructs the baseline replay from the original immutable artifact manifest;
it does not persist provider credentials or a copied Secret-bearing environment.

## Commands

Run an initial bounded validation-search:

```bash
agentskill-eval optimize v2 smoke SPEC \
  --workspace WORKSPACE \
  --confirm-real-run \
  --max-agent-runs N \
  --max-cost-microusd N
```

Inspect a partial or completed session:

```bash
agentskill-eval optimize v2 status WORKSPACE
```

Resume only pending candidates:

```bash
agentskill-eval optimize v2 resume SPEC \
  --workspace WORKSPACE \
  --confirm-real-run \
  --max-agent-runs N \
  --max-cost-microusd N
```

`resume` always requires a new `--confirm-real-run`, `--max-agent-runs`, and
`--max-cost-microusd`. It does not reuse a prior authorization or silently expand a budget.
The new budget applies only to new pending work; the JSON and HTML reports retain cumulative
observed run and cost totals.

An explicit `resume` may re-attempt a fully observed provider-blocked candidate after the
operator has fixed the external condition (for example, balance or a rate limit). That retry
gets a new immutable runner attempt identity and retains the prior provider error in the
session history. Normal `smoke`/`run` never performs this retry automatically.

## Resume Validation

Before any new Agent invocation, resume recreates the no-cost preflight and compares these
immutable inputs against the session:

- Optimization v2 spec and verified Proposal manifest
- base Skill and every accepted candidate Skill hash
- failure bundle and published validation-search dataset hash
- complete Agent configuration hash
- actual Runner executable SHA-256

Any mismatch rejects the resume. This includes changed Proposal artifacts, candidate files,
dataset content, Agent configuration, or Runner binary.

## Partial Outcomes

The report records completed, invalid, provider-blocked, and remaining candidates. Per-case
ledger entries distinguish:

- `task_failed`: the Agent produced a valid evaluated task failure; it remains a valid
  comparison observation.
- `agent_invalid`: invalid Agent or Runner result.
- `insufficient_balance`: provider rejected the request for insufficient balance.
- `rate_limited`: provider rate limit.
- `provider_timeout`: provider timeout.
- `budget_exhausted`: the current authorization cannot start more work.

DeepSeek HTTP 402 is always `insufficient_balance`. It is provider-blocked, contributes no
candidate win/tie/loss, and the session is not automatically retried. A provider block with a
complete auditable pair is eligible only for the explicit `resume` path described above.

If a pair ends partway through because of a budget or cancellation, the runner persists every
observed Run, token/cost measurement, trace and baseline replay count before returning. The
partially observed candidate is terminal and is never replayed automatically, because doing so
could duplicate provider work. A later `resume` can still continue other untouched pending
candidates. `baseline_reused_runs` is the actual count reported by the runtime, not a value
inferred from planned versus observed Run totals.

An invalid baseline invalidates its candidate comparison: the report withholds pass-rate gain
and W/T/L rather than treating a valid treatment alone as evidence of Skill improvement.

Reports remain validation-search-only evidence. They do not justify a winner, Skill improvement,
regression, confirmation, locked-test, generalization, or release claim when partial work,
invalid runs, or provider blocks exist.
