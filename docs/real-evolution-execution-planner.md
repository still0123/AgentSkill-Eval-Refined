# Stage 3A: Real Evolution Execution Planner

Stage 3A freezes the execution and budget contract for the real Skill evolution chain without
calling a model, launching an Agent, or reading locked-test case content.

```text
verified Proposal
→ validation_search
→ regression_dev
→ validation_confirm
→ one locked_test checkpoint
→ human review
→ publish Skill v2
```

This is planning evidence only. It does not claim that a candidate is better, that the current
Independent Final Evaluator already uses the observed-Agent adapter, or that a real Skill v2 is
ready to publish.

## Inputs

The planner requires:

- a verified `proposal-manifest.json` directory;
- one frozen `RealAgentEvidenceSpec` for provider, model, Runner, executable hashes, tools,
  pricing, token estimates, and environment policy;
- metadata-only descriptors for `validation_search`, `regression_dev`, `validation_confirm`, and
  `locked_test`;
- a shared split-plan hash and a distinct DatasetVersion hash for every split;
- an explicit run and cost ceiling for each paid stage.

The locked descriptor intentionally has no filesystem path. It contains only the split name,
DatasetVersion hash, split-plan hash, counts, and `content_access: metadata_only`. Stage 3A cannot
open locked Case content.

Template: [`real-evolution-plan.example.yaml`](../examples/optimizer/evolution-plan/real-evolution-plan.example.yaml).

## Exact run calculation

Let:

- `P` be the real LLM proposal count;
- three built-in comparators be original, manual, and random;
- `S` be the search subset size;
- `V` be the full `validation_search` Case count;
- `K` be generated candidates promoted after the subset;
- `R`, `C`, and `L` be regression, confirmation, and locked Case counts.

The current paired real evaluator uses two Agent runs for each candidate/Case evaluation:

```text
search evaluations = (P + 3) × S + (3 + K) × (V - S)
search Agent runs   = 2 × search evaluations
regression runs     = 4 × R
confirmation runs   = 4 × C × confirmation repeats
locked runs         = 4 × L × locked repeats
```

The factor four in the last three stages is `base/winner × baseline/treatment`. Cost and token
envelopes use the frozen per-run estimates in `RealAgentEvidenceSpec`.

## CLI

Preflight calculates the complete plan in memory and writes nothing:

```bash
agentskill-eval evolution plan preflight CONFIG
```

Prepare writes immutable artifacts and is idempotent for identical inputs:

```bash
agentskill-eval evolution plan prepare CONFIG \
  --workspace .agentskill-eval/evolution-plan
```

Inspect and verify:

```bash
agentskill-eval evolution plan inspect PLAN_DIRECTORY
agentskill-eval evolution plan verify PLAN_DIRECTORY
```

The output directory contains:

```text
evolution-execution-plans/PLAN_ID/
├── execution-plan-manifest.json
├── execution-plan.json
└── execution-plan.md
```

Changing either output artifact causes verification to fail.

## Authorization boundary

`prepare` never accepts a real-run confirmation flag because it cannot execute anything. Every
paid stage in the plan has `explicit_authorization_required: true`; the locked stage additionally
has `locked_receipt_required: true`. Stage 3B must ask for separate authorization immediately
before each stage and stop creating runs when that stage's ceiling is reached.

## Capability gaps carried by the plan

Before execution:

1. Stage 2 must bind published immutable DatasetVersions to the metadata descriptors.
2. Execution must recheck the Agent/Runner versions and executable hashes and capture the actual
   environment fingerprint before authorization.
3. Independent Final Evaluation must use the same paired observed-Agent adapter as search.
4. The locked receipt must be reserved and consumed exactly once.
5. A proposal based on `simulated_fixture` remains synthetic proposal evidence; the field is
   preserved and cannot be presented as observed train evidence.

These are explicit plan requirements, not silently assumed completed features.
