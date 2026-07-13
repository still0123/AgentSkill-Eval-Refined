---
name: python-review-manual
description: Manually extended evidence-first Python defect review.
version: 1.1.0-manual
---

# Python Review

Use this workflow when asked to inspect Python code for a concrete defect.

1. Read the requested production files and their direct callers or tests before concluding.
2. Trace real values across boundaries; do not infer behavior from comments, filenames, or deprecated
   directories alone.
3. Check nullability, index boundaries, resource lifetime, exception propagation, cross-module data
   contracts, configuration parsing, and retry budgets.
4. Try to disprove each suspected defect. Existing guards, context managers, deliberate test failures,
   dead code, and comments containing words such as BUG are not defects by themselves.
5. Report one primary category only when a reachable path and concrete consequence are both supported.
6. For resources, verify cleanup on every exception path before reporting a leak.

Use one of these stable categories when applicable:

- `NULL_DEREFERENCE`
- `RESOURCE_LEAK`
- `OFF_BY_ONE`
- `SWALLOWED_EXCEPTION`
- `CACHE_KEY_MISMATCH`
- `NORMALIZATION_MISMATCH`
- `BOOLEAN_PARSE_ERROR`
- `RETRY_BUDGET_EXCEEDED`

Output `DEFECT_FOUND: <CATEGORY>` with file, symbol, evidence, consequence, and a minimal fix. If the
requested code has no supported actionable defect, output `NO_ACTIONABLE_DEFECT` and briefly state the
evidence that ruled out likely false positives. Never invent a defect to satisfy the task.
