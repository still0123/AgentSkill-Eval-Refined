# Test Generation Negative-Result Diagnosis

## Decision

The recorded Test Generation result remains:

- 2 independent Git-history Cases;
- 4 observed Agent Runs;
- 0 Runner-level `INVALID`;
- paired W/T/L `0 / 2 / 0`;
- no generated `agent_regression_test.py`.

Post-hoc Trace analysis changes its interpretation. The four task outcomes are valid `FAIL`
records, but the comparison is **confounded and cannot establish that the Test Generation Skill
had no effect**. The Process Agent was specialized for production-code bug fixing, contradicted
the Test Generation task, and did not expose the installed Skill content to the model.

This diagnosis used retained artifacts only. It made no Proposal call, Agent call, Skill change,
Dataset change, grader change, or Case addition.

## Evidence set

Frozen experiment:

```text
Experiment: 855ded69-a605-526d-9fd8-c84b5ee2dc27
DatasetVersion: d90135df-d060-562a-9252-29322cc99847
Dataset SHA-256: eb27c384f0fed3fd136a0456bbcffc70829d8d1810c27952035bfbe2935f5390
Agent SHA-256: 0f7d7db41861363277efe10a7dd14483e5355ea3b67b42de8d5b16158c5554de
Skill SHA-256: d1cfd04f321985620117316909ef12807a90f5740aa6161dcc6514f2e271b3f7
```

Run summary:

| Run | Arm | Case | Turns | Tool calls | Terminal signal | Writes |
|---|---|---|---:|---:|---|---:|
| `04cb4c3a-0157-56f5-b4e1-40299ba8415a` | treatment | more-itertools | 6 | 5 | empty assistant message | 0 |
| `55a3bd00-cf63-5250-bdc3-3eab5dadf037` | treatment | cachetools | 9 | 13 | cumulative input-token limit | 0 |
| `cb3a6cc8-7744-5833-b391-8ffbfe0a73b1` | baseline | more-itertools | 8 | 7 | empty assistant message | 0 |
| `f0b48ec0-ec6e-5a37-9ca1-0b495da4b0f9` | baseline | cachetools | 12 | 13 | model-turn limit | 0 |

All 38 observed tool calls were read or search operations. No Run called `write_file`,
`replace_in_file`, or a command targeting `agent_regression_test.py`.

## Root cause

### 1. Task-family instruction conflict

The user task explicitly required:

```text
Write agent_regression_test.py.
Do not modify production code.
Run the generated test before finishing.
```

The frozen Process Agent supplied higher-priority instructions that required the opposite:

- implement the production bug fix;
- edit the source with `replace_in_file`;
- do not create a temporary reproduction test;
- after initial inspection, emit another mandatory production-edit nudge.

See `examples/real-agent-evidence/qwen_openai_process_agent.py:268-324` and `:455-467`.
Because both Test Generation Case IDs still contain `more-itertools` or `cachetools`, they also
activated the Bug Fix-specific Case hints.

This explains the common behavior across both arms: the model repeatedly inspected production
files and never started the requested test artifact.

### 2. Treatment Skill was installed but not delivered to the model

The treatment compiled eval contains:

```yaml
skills:
  - path: skills/selected
```

and the platform correctly records the frozen Skill hash as installed. That is installation
evidence, not activation evidence.

The Custom Engine SessionInput contains only:

```text
case_id, variant, workspace, model, messages, max_turns, timeout_seconds
```

It contains no Skill content or Skill path. The Process Agent consumes `workspace`, `case_id`,
textual `messages`, and limits; it does not read a Skill field or discover installed Skill files.
No `SKILL.md` was present in the Agent workspace or isolated HOME.

For each Case, baseline and treatment model messages were byte-identical:

```text
more-itertools messages SHA-256:
26d1198e18adca68ea7c3416c8360d41e8e990f93ba83eb324c74fc6dc632332

cachetools messages SHA-256:
24fd1bc828cb9074f085dc0c2b4f19c1a53cf237b3d5c8326037130d04cc9717
```

The treatment therefore did not establish the intended experimental variable at model-input
level.

## Contributing factors

### Unfocused file reads

The model requested `read_file` with `offset` and `limit`, but the tool schema accepts only `path`
and the implementation always returns up to 12 KB from byte zero. This caused repeated large,
misaligned reads and contributed to token/turn exhaustion.

See `qwen_openai_process_agent.py:64-68` and `:129-140`.

### Empty completion accepted as normal Agent exit

When the provider returns neither tool calls nor non-empty content, the Process Agent accepts the
empty string as `final_message` and exits with code 0. Both more-itertools Runs terminated this way
before any artifact was created.

See `qwen_openai_process_agent.py:410-424`.

### Workspace diff is not absence evidence

The captured `workspace_diff` points at pre-existing changes in the enclosing AgentSkill-Eval
worktree rather than the fixture repository. It also cannot represent an untracked generated test.
The missing-file conclusion is instead supported by the direct workspace scan and deterministic
grader.

## Hypothesis disposition

| Hypothesis | Result |
|---|---|
| Agent never attempted a write | confirmed |
| Agent wrote the wrong filename/path | rejected |
| Evidence collection lost a successful write | rejected |
| Prompt/Skill handoff was conflicting or absent | confirmed |
| Execution stopped before writing | confirmed |

## Correct conclusion boundary

Use the following wording:

> Under the frozen Test Generation protocol, all four observed Runs failed to create the required
> test file, producing paired W/T/L 0/2/0. Post-hoc Trace analysis found a task-family Runtime
> mismatch and an unproven Skill handoff, so this is a confounded execution result rather than
> evidence that the Test Generation Skill itself provides no benefit.

Do not describe this experiment as a valid negative Skill-efficacy result.

## Deferred work

No fix is part of this diagnostic change. A later, separately authorized implementation should:

1. provide a Test Generation-specific Agent system contract;
2. make frozen Skill content or an equivalent hash-bound handoff explicit in model input;
3. fail preflight when task instructions conflict with the Agent system contract;
4. honor focused `read_file` ranges;
5. reject empty completion before the required output artifact exists;
6. verify the corrected harness with deterministic stubbed-model tests before any paid replay.

The same two frozen Cases should be retained for the first corrected replay. No additional Case is
needed to validate the harness.

## Follow-up

The task-specific contract, hash-bound treatment Skill handoff, focused reads, empty-completion
guard, and deterministic tests were implemented. The same two Cases were replayed once: 4 Runs,
0 INVALID, W/T/L `0 / 2 / 0`. The corrected harness therefore establishes no gain on these two
Cases. See [Test Generation Runtime Fix and Corrected Replay](./test-generation-runtime-fix.md).
