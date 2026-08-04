# Real Positive Skill Loop Evidence

This directory contains the sanitized public summary of one bounded observed-Agent attempt.
The attempt obtained a real Skill v1 task failure but stopped at validation_search because neither
of the two generated candidates improved PASS rate.

## Outcome

- real v1 failure: yes;
- Proposal candidates: 2;
- validation_search W/T/L for each candidate: `0 / 1 / 0`;
- regression, confirmation, locked test: not executed;
- immutable Skill v2: not published;
- Test Generation Skill: not started.

See [`docs/real-positive-skill-loop.md`](../../docs/real-positive-skill-loop.md) for the evidence
interpretation and stop decision.

## Full local evidence

The complete artifacts remain in the ignored workspace
`.agentskill-eval/real-positive-loop/`. They contain Trace, deterministic pytest output, Skill
activation, security scans, Proposal evidence, search reports, and replayable tar bundles.

| Bundle role | Size bytes | SHA-256 |
|---|---:|---|
| initial 401 INVALID attempt | 880640 | `cae89c8cbbd08bbc1ced1f4d96cb58118fbfa08d5fa24dcbead1f4b30726b55c` |
| valid train replay | 1085440 | `eb5fcfadfddfaeabc940c03fede1e402d1346e19f48007919c76f6ce13cd8049` |
| search Skill v1 | 737280 | `60fce53a9be5b2f46c1b6967a7b60a92e4ec249057ab43f71662c9a1821b5946` |
| search test-command candidate | 747520 | `95cc04659943afde1a5aaa5ab8891abe128a777bf145831bf818148e67c9e038` |
| search environment candidate | 757760 | `125ea9f2c89d65308945ad9c0e702e831916870c0ccf0b4c9ee018753ed3682f` |

The full bundles are intentionally not committed because they contain complete public source
fixtures and total several megabytes. Their digests bind this summary to the retained local
artifacts.

## Evidence boundary

All successful train and search Runs are `observed_agent`, not simulated. The cases originate from
public Git history and have high contamination risk. The result does not establish general Skill
performance and must not be presented as a successful v1-to-v2 release.
