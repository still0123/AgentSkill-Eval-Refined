---
name: python-bug-fix-v1
description: Evidence-first workflow for minimal Python defect fixes.
version: 1.0.0
---

# Python Bug Fix

Use this workflow when asked to repair a concrete defect in an existing Python repository.

1. Read the task, the relevant production code, and the closest tests before editing.
2. Reproduce the failure with the narrowest deterministic offline test command available.
3. Trace the actual value and control flow; distinguish the root cause from the observed symptom.
4. Make the smallest production-code change that repairs the general contract. Do not special-case test
   names, fixture values, repository revisions, or expected patches.
5. Run the targeted regression test after the edit. If it fails, inspect the new evidence before making
   another change; do not repeat the same edit blindly.
6. Run adjacent tests when the remaining budget permits and report any unverified scope explicitly.
7. Preserve public behavior outside the requested defect and avoid unrelated formatting or refactors.

The final response must summarize the root cause, changed files, verification commands and outcomes.
Never claim success without observed test evidence. Never reveal credentials, hidden reasoning, or
private environment data.

## Candidate guidance

<!-- mutation:require-post-fix-verification; hypothesis:The Agent omitted verification because the Skill does not require confirming that the fix resolves the failure before reporting success. -->
- After making a change, always run the reproduction command again and confirm it passes. If it fails, iterate on the fix before proceeding.
