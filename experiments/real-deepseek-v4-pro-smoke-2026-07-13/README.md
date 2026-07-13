# DeepSeek V4 Pro observed-Agent smoke evidence

This directory contains the sanitized, reviewable record of the first fully successful
observed-Agent smoke experiment. It does not contain API keys, absolute local paths, Qwen caches,
session transcripts, raw Runner logs, or the replay bundle.

The experiment ran two real Git-history bug-fix cases once per arm with Qwen Code 0.19.9,
DeepSeek V4 Pro, and skill-up 0.5.0. All four Runs completed and passed. Both cases were positive
ties, so this smoke validates the real execution and evidence chain but does not show incremental
Skill benefit. With only two cases from one repository, the result is descriptive evidence only.

The treatment used about 20.1% more tokens and cost about 8.7% more than baseline. The total
observed cost was 75,207 micro-USD against a 250,000 micro-USD authorization. All four persisted
Secret scans were clean, the exact Key scan found no matches, thinking tokens were zero, and the
241-file replay bundle verified successfully.

`config.sanitized.yaml` freezes the reproducible non-secret configuration. `result.sanitized.json`
contains aggregate metrics and hashes of the local reports and audit bundle. Raw evidence remains
local and intentionally untracked because it may contain model output, source workspaces, caches,
or machine-specific paths.
