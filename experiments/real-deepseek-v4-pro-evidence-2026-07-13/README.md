# DeepSeek V4 Pro observed-Agent evidence experiment

This directory contains the sanitized, reviewable record of the first 12-Run observed-Agent
evidence experiment. It does not contain API keys, absolute local paths, Qwen caches, session
transcripts, raw Runner logs, or the replay bundle.

The experiment ran two real Git-history bug-fix cases with three repeats per arm using Qwen Code
0.19.9, DeepSeek V4 Pro, and skill-up 0.5.0. Nine Runs completed and passed; three Runs were
classified invalid after the Runner returned `ERROR`/`execution_error`. Invalid Runs were retained
and were not automatically retried. Baseline passed 4/6 assigned Runs and treatment passed 5/6,
for a descriptive absolute gain of 16.7 percentage points and W/T/L of 1/1/0 across the two cases.

Treatment used about 6.2% fewer tokens, 19.7% less latency, and 5.0% less cost on the paired
aggregate. Total observed-or-reserved cost was 231,195 micro-USD against a 750,000 micro-USD
authorization. All twelve persisted Secret scans were clean, an exact Key scan across 12,341 local
files found no matches, thinking tokens were zero, and the 389-file replay bundle verified.

These are descriptive observations from two cases in one repository. They do not support a claim
that this Skill generally improves Agent performance, and the three invalid Runs materially limit
the comparison. `config.sanitized.yaml` freezes the non-secret configuration;
`result.sanitized.json` contains aggregate metrics and hashes. Raw evidence remains local and
untracked because it may contain model output, source workspaces, caches, or machine-specific paths.
