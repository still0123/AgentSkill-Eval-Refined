# Optimization Evaluation v2

Optimization Evaluation v2 evaluates a generated Skill candidate against its parent
Skill version directly. It is the first execution stage after proposal generation:

```text
Skill v1 + Case
       │
       ├── baseline (v1)
       └── treatment (candidate v2)
                │
                └── validation-search evidence
```

The stage answers a narrow question:

> Does this candidate improve the frozen validation-search Cases over the exact same
> Skill v1, Agent, model, Runner and environment?

It does not publish a Skill version and it does not run regression, confirmation or
locked tests.

## Inputs

The plan is `examples/optimizer/failure-guided/optimization-v2.example.yaml`.
It binds:

- the immutable base Skill v1;
- a verified real Proposal artifact;
- the observed train failure bundle used to produce the Proposal;
- one `validation_search` DatasetVersion and exactly two Case IDs;
- one real Agent configuration, provider and model;
- a maximum of three quality-gated candidates and twelve logical Agent Runs.

The Proposal evidence must be aligned with the target Agent. A bundle created by
another provider or model is rejected during preflight. This prevents a DeepSeek
candidate from being justified by unrelated Qwen failures.

## Offline preflight

```bash
.venv/bin/agentskill-eval optimize v2 preflight \
  examples/optimizer/failure-guided/optimization-v2.example.yaml \
  --workspace .agentskill-eval/optimization-v2-preflight
```

Preflight verifies Proposal hashes, parent Skill hash, candidate novelty and
actionability, case membership, Agent/model alignment, DatasetVersion hash and the
budget. It writes JSON and offline HTML reports and makes no model calls.

The current example intentionally reports `INSUFFICIENT` until a real DeepSeek
observed-train failure bundle is supplied. That is a safe evidence boundary, not a
failed experiment.

## Candidate materialization

Accepted candidates are immutable directories containing `SKILL.md` and
`metadata.yaml`. The quality gate rejects near duplicates, benchmark/case leakage,
guidance that requires tools not declared by the Runner, and non-actionable text.
Every accepted candidate records its parent hash, candidate hash and Proposal
evidence references.

## Bounded real screening

After preflight is `READY`, the real stage can be authorized explicitly:

```bash
.venv/bin/agentskill-eval optimize v2 smoke CONFIG \
  --workspace WORKSPACE \
  --confirm-real-run \
  --max-cost-microusd VALUE \
  --max-agent-runs 12
```

The first candidate runs a four-Run paired smoke (two Cases × v1/candidate). If any
Run is invalid, screening stops and no later candidate is called. For later
candidates, the already observed v1 result for each Case is replayed from an
immutable baseline cache; only the candidate treatment Runs are new. With three
candidates, the logical ceiling is twelve Runs and the expected observed ceiling is
eight paid Agent Runs (`4 + 2 + 2`). The report records `reused_runs` and zero cost
for replayed baseline Runs.

The report files are:

```text
optimization-v2-screening.json
optimization-v2-screening.html
```

Each candidate includes v1 and treatment Case results, pass rates, token/latency/
cost measurements, trace references and the claim limit. HTML is escaped and uses a
`default-src 'none'` Content-Security-Policy so it opens offline without loading
external scripts.

## Evidence limits

This stage is descriptive validation-search evidence. Even a completed run cannot
claim general Skill improvement from two Cases. It cannot authorize or imply:

- regression-dev protection;
- independent confirmation;
- locked-test performance;
- Skill v2 publication;
- generalization to another Skill family.

Those claims require the later independent stages and their own explicit budgets.
