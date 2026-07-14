# Interactive Scenario Agent Loop MVP

## Purpose

The interactive loop evaluates whether a Process Agent can observe an environment result and
choose its next action. It upgrades the existing one-shot `AgentPlan` integration without changing
the deterministic MCP and Memory/RAG graders.

This stage remains `simulated=true` and `evidence_class=process_integration`: the Agent process is
real and externally invoked, while the MCP, retrieval, and Memory environments are deterministic
local labs. Results are integration evidence, not population-level model evidence.

## Interaction modes

`process_agent.interaction_mode` is frozen into the evaluation plan:

- `plan_once` (default): one process returns the complete legacy plan.
- `step_loop`: one fresh process returns exactly one action per step, receives bounded prior
  Action/Observation history, and eventually returns `final`.

The default preserves all previous scenario configurations and reports.

```yaml
process_agent:
  name: your-process-agent
  version: 1.0.0
  executable: /absolute/path/to/process-agent
  expected_sha256: <64 lowercase hex characters>
  expected_version_output: your-process-agent 1.0.0
  interaction_mode: step_loop
  max_steps: 12
  max_history_events: 24
  max_observation_bytes: 100000
```

## Versioned wire protocol

Each step starts a new process without a shell. The request uses
`ase/interactive-agent-request/v1alpha1` and includes the scenario, a non-oracle task view, Skill
content only for treatment, the frozen step limit, and bounded Action/Observation history.

The response must contain exactly:

```json
{
  "schema_version": "ase/interactive-agent-response/v1alpha1",
  "action": {"kind": "final", "answer": "completed"}
}
```

Extra keys such as `reasoning`, unsupported versions, oversized JSON, or invalid action fields fail
the run. The platform neither requests nor persists chain-of-thought.

## Supported actions

MCP supports `tool_call` and `final`. Memory/RAG supports `retrieve`, `memory`, and `final`.
The Agent never receives expected tools, oracle answers, gold documents, Memory expectations, or
grader rules.

## Execution and safety

- Executable path, SHA-256, version output, environment allowlist, timeout, response size, step
  count, history count, and observation bytes are frozen.
- Symbolic links and Secret-like inherited environment variable names are rejected.
- Timeout kills the isolated process group. Process failures never fall back to a precompiled plan.
- Baseline steps never receive Skill content; treatment receives the verified immutable Skill.
- MCP side-effect and confirmation policies run before the environment call.
- Step and tool-call budgets terminate loops deterministically.
- Raw retrieval text and Memory values only exist in ephemeral next-step requests. Persisted
  interaction evidence contains hashes and redacted summaries.

## Evidence and compatibility

The native MCP or Memory/RAG trace remains the source for existing graders. In addition, every
decision is recorded in `process-agent-decisions.json`, and redacted session/action/observation
events are written to `interactive-agent-traces.json`.

The unified plan advertises `agent_observation_loop` and freezes `interaction_mode` plus
`max_interaction_steps`. Re-running the same frozen plan returns its immutable result without
invoking the Agent again. Existing `plan_once` configurations remain valid.

## Commands

```bash
agentskill-eval scenario validate examples/unified/mcp-tool.step-loop.example.yaml

agentskill-eval scenario run examples/unified/mcp-tool.step-loop.example.yaml \
  --workspace .agentskill-eval \
  --allow-simulation

agentskill-eval scenario report WORKSPACE EXPERIMENT_ID
```

Replace the example executable and SHA-256 before validation.

## Interpretation limits

This MVP demonstrates observable adaptation in deterministic local environments: using tool
results, retrying after injected failure, grounding an answer in retrieved documents, and executing
Memory operations step by step. It does not establish general model improvement. That requires the
separate real-agent protocol, repeated paired runs, explicit cost authorization, and
`simulated=false` evidence.
