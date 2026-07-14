# DeepSeek V4 Pro Real Optimizer smoke

This directory contains the sanitized record of the Stage 2 Real Optimizer Evaluator smoke. It
does not contain API keys, absolute local paths, model transcripts, source workspaces, Runner
caches, or replay bundles.

The search evaluated six frozen candidates on two real Git-history bug-fix cases through Qwen
Code 0.19.9, DeepSeek V4 Pro, and skill-up 0.5.0. Successive halving executed ten unique
candidate/case evaluations. Every evaluation used a without-Skill baseline and a with-Skill
treatment, producing 20 real Agent Attempts. Eighteen Attempts completed and two were retained as
invalid: one exceeded the 24 tool-call limit and one triggered loop detection.

The search froze `search-protocol-boundaries`. Its treatment passed both cases, compared with one
of two for the original Skill. A random control candidate also passed both cases. Within the
winner's own paired experiment, both baseline and treatment passed both cases. Therefore this is
only evidence that the real optimizer execution and selection chain works; it is not independent
evidence that the frozen candidate generally improves the Skill.

Observed-or-reserved cost across the five unique Skill experiments was 460,557 micro-USD, below
the 1,300,000 micro-USD authorization. All five replay bundles verified (1,216 files total), and a
post-run Secret-pattern scan found zero matching files. Raw evidence remains local and ignored
because it includes model output, source fixtures, caches, and machine-specific paths.

`config.sanitized.yaml` freezes the non-secret protocol. `result.sanitized.json` records the
candidate decision, aggregate usage, invalid classifications, and audit hashes.
