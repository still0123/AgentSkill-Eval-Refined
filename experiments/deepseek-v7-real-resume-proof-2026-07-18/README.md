# DeepSeek V7 Real Resume Proof

This record covers the provenance repair and planned two-segment real resume proof.
The observed train bundle was derived without modifying its parent evidence, and the
Optimization v2 preflight completed successfully. No DeepSeek request was started because
the required `OPENAI_API_KEY` was absent from the execution environment before the first
provider request.

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
- Accepted candidates: 4
- Logical plan: 16 Agent Runs
- Expected new Runs with baseline reuse: 10

The preflight does not invoke a model, regression, confirmation, locked test, or release step.

## Real Execution Boundary

The authorized envelope was two segments of at most four Agent Runs each, with a combined
maximum of 8 Runs and `2,800,000` microusd. The first real segment was not started because
the local no-cost Agent preflight found `OPENAI_API_KEY` missing. Per the authorization
constraints, no provider/auth retry was attempted and no resume command was issued.

- First segment actual Agent Runs: 0
- Resume segment actual Agent Runs: 0
- Total actual Agent Runs: 0
- Total cost: 0 microusd
- Baseline reuse from this planned run: 0
- Completed/invalid/provider-blocked candidates: 0 / 0 / 0
- Run keys executed by this planned run: none

## Claim Limit

This artifact proves the derived observed-failure provenance path and the no-cost
Optimization v2 preflight. It does not prove a real interruption/resume, candidate improvement,
a winner, regression performance, confirmation, locked-test behavior, generalization, or a
Skill release.
