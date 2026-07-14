# Stage 3B: Real Evolution Dry-Run Orchestration

Stage 3B connects the immutable Stage 2 Benchmark release to the Stage 3A execution plan without
calling a model, launching an Agent, or opening confirmation/locked Case content. It proves that the
future real execution can bind the intended DatasetVersions and traverse the adaptive-stage process
boundary while retaining the holdout isolation policy.

```text
verified Stage 3A plan
        +
verified Stage 2 release / optimizer view
        ↓
bind validation_search → local Process rehearsal
        ↓
bind regression_dev    → local Process rehearsal
        ↓
validation_confirm receipt only
        ↓
locked_test receipt only
        ↓
AWAITING_REAL_AUTHORIZATION
```

This is `simulated=true`, `evidence_class=process_integration_dry_run`. It is not an Agent
evaluation and cannot support a Skill-improvement claim.

## Security and isolation boundary

The orchestrator is a trusted binding component. It reads the Stage 2 release manifest to compare
all four evaluation descriptors with Stage 3A, but it opens and integrity-checks only
`validation_search` and `regression_dev` DatasetVersion directories.

For `validation_confirm` and `locked_test`, it:

- compares only hash/count metadata;
- derives the same release-bound receipt used by the optimizer view;
- never resolves or opens the DatasetVersion path;
- never persists the path or candidate keys in Stage 3B evidence;
- never invokes the rehearsal Process for either stage.

The local Process receives only stage name, DatasetVersion/split-plan hashes, counts, and dry-run
identity. It does not receive a filesystem path, Case ID, prompt, fixture, grader, Skill content,
credential, or hidden reasoning. Its executable and version are frozen, it runs without a shell,
and it inherits only an explicit non-secret environment allowlist.

## Configuration and CLI

Copy
[`evolution-dry-run.example.yaml`](../examples/optimizer/evolution-plan/evolution-dry-run.example.yaml)
and replace its Stage 2/3A paths. The checked-in Process fixture is deliberately local and
deterministic.

Preflight validates bindings and executes the two metadata-only Process acknowledgements without
writing evidence:

```bash
agentskill-eval evolution dry-run preflight CONFIG
```

Prepare writes an immutable, content-addressed result:

```bash
agentskill-eval evolution dry-run prepare CONFIG \
  --workspace .agentskill-eval/evolution-dry-run
```

Inspect and verify:

```bash
agentskill-eval evolution dry-run inspect DRY_RUN_DIRECTORY
agentskill-eval evolution dry-run verify DRY_RUN_DIRECTORY
```

The output is:

```text
evolution-dry-runs/DRY_RUN_ID/
├── dry-run-manifest.json
├── dry-run-report.json
├── dry-run-report.md
├── adaptive-bindings.json
├── withheld-receipts.json
└── process-evidence.json
```

Identical `prepare` calls return the existing verified directory and do not repeat the stage
rehearsal. Changing any evidence file causes `verify` to fail.

## What Stage 3B proves

- the four Stage 3A dataset descriptors match one immutable Stage 2 split plan;
- adaptive DatasetVersions are published, present, and integrity-valid;
- optimizer-view visibility exactly matches the release;
- the Process boundary can traverse both adaptive stages with frozen input metadata;
- confirmation and locked stages remain withheld;
- the next transition is an explicit real-run authorization checkpoint.

It does not prove that a proposal is useful, the Agent can solve a Case, a winner passes regression,
or Skill v2 is publishable. A later real-execution stage must re-run Agent/Runner preflight, ask for
an explicit paid-stage authorization, enforce budgets, and consume locked evidence at most once.
