# DeepSeek V7 Real Resume Proof

This record covers the provenance repair and two-segment real resume proof. The observed
train bundle was derived without modifying its parent evidence, and the Optimization v2
preflight completed successfully before the real execution.

## Provenance

- Derived bundle SHA-256: `e9e34ff88c402753d0feb07a91435a41159b2ace1ba2d4589beeccba37e143d3`
- Parent bundle SHA-256: `2f1da987579925a24071675083eb002508bd8463f1465a5f8bd1ab281c63ffa8`
- Source experiment SHA-256: `01be6204a76f59fd76ecf3c4994cc592dcc6403c579685cf8e0abde159cae22f`
- Source report SHA-256: `c9438a214b3372c2c7fefd529d42e7d6ed587cfc7fe11764658e370ff0ed7f00`
- Provider/model: `deepseek` / `deepseek-v4-pro`
- Runner: `skill-up version 0.5.0` /
  `b8473aad3fe997f3aa8de1e9bd9bc127e5254b25371567a0e07143afc809c359`
- Agent config SHA-256: `1e06a3205550ed6393d4d1ae5b9a7c4cfbc47808fb949e7007753f68db02482c`
- DatasetVersion SHA-256: `7f173e954299b6b2054d174ee6bc6d22a1274cf5a4063723b48327428df5e764`

The derived bundle's source attempts had clean persisted security scans. The current exact
Secret value was unavailable, so no Secret value was read or written while deriving this
receipt.

## Optimization V2 Preflight

Proposal v7, the base Skill, the derived provenance bundle, the frozen dataset, and the
real Agent configuration passed no-cost preflight:

- Proposal manifest SHA-256: `cdab8d0aafbb67e25f3e22439e42ae0f5a129efd9d3377db9bc7eeb17d129d85`
- Base Skill SHA-256: `5ff780e023c00cd08232688ec013a47f51926b4e1c8a5171465085ee967bc5d6`
- Session Agent config SHA-256: `0d663c9e56b04b79a8cc3d75d28e6a97f11aea1a8ff9565e0316bcd5e3a176b0`
- Accepted candidates: 4
- Logical plan: 16 Agent Runs
- Expected new Runs with baseline reuse: 10

The preflight does not invoke a model, regression, confirmation, locked test, or release step.

## Real Resume Execution

The authorized envelope was two segments of at most four Agent Runs each, with a combined
maximum of 8 Runs and `2,800,000` microusd. `OPENAI_API_KEY` was loaded from the local
keychain only into each command process and was not printed or persisted.

1. The initial `optimize v2 smoke` authorization was 4 Runs and `1,400,000` microusd. It
   persisted session `159907907cf896b90c15561c5294725c4c72837ce2f73490cfc901c5cba62ec1`
   after 4 real provider runs, completing `enforce-post-edit-test-rerun`.
   - Input/output tokens: 2,462,413 / 13,210
   - Latency: 177,896 ms
   - Cost: 91,241 microusd
2. `optimize v2 status` reloaded the same durable session with three pending candidates.
3. A new `optimize v2 resume` authorization, also limited to 4 Runs and `1,400,000`
   microusd, completed `validate-test-coverage` and
   `parse-test-output-for-failures`.
   - Input/output tokens: 2,788,632 / 14,518
   - Latency: 201,735 ms
   - Cost: 94,257 microusd

The session stopped with `BUDGET_EXHAUSTED` because each segment reached its Run-count
authorization, not because of provider blocking or monetary exhaustion. The remaining
candidate is `invalidate-test-cache`; it was deliberately not run.

## Resume Proof

- Actual provider runs: 8 of the authorized 8; total cost: 185,498 microusd.
- Total input/output tokens: 5,251,045 / 27,728; total latency: 379,631 ms.
- Completed/invalid/provider-blocked candidates: 3 / 0 / 0.
- Baseline replays: 4. The two baseline runs from the first segment were reused for the
  next two completed candidates, and no baseline provider call was repeated.
- The durable ledger contains 10 distinct run keys: 8 executed and 2 still pending.
  The duplicate run-key set is empty.
- The final partial report is
  `.agentskill-eval/observed-provenance/v2-preflight/optimization-v2-screening.json`
  (SHA-256 `7ea6e0246bae28d3038ca911a8ceced2f16a02822694019719a87d7026511ee8`).
  The session SHA-256 is
  `75db1eb0692de1e1257a8ada085ce6c6375297a401851d8ff5834a848774f2ea`.

The runtime evidence tree contains 12 immutable trace files: 8 from the actual provider
runs and 4 materialized baseline replays. Its aggregate SHA-256 is
`0c860665e0064d589a27c965ef00e31c8b295ec281d95c44b117e195637b1765`.
All 12 persisted attempt Secret scans are `clean`, with zero matched Secret names.

After the resume CLI had emitted its durable result, the host terminal reported a local
`/Library/Python` sandbox restriction. A separate `optimize v2 status` reload confirmed
the unchanged persisted ledger, accounting, and absence of provider or Agent errors; no
additional provider call or retry was made.

## Claim Limit

This artifact proves the derived observed-failure provenance path, real two-segment
interruption/resume, baseline reuse, unique executed run keys, and non-duplicated accounting.
It does not establish candidate improvement, a winner, regression performance, confirmation,
locked-test behavior, generalization, or a Skill release.
