---
name: python-test-generation-v1
description: Evidence-first workflow for writing Python regression tests without production edits.
version: 1.0.0
---

# Python Regression Test Generation

Use this workflow when asked to write a regression test for an existing Python defect.

1. Read the defect description, relevant production code, and nearby tests before writing anything.
2. Do not edit production files. Put all new behavior checks in the requested test file.
3. Reproduce the defective behavior with the smallest deterministic offline setup.
4. Assert the public contract that should hold after the defect is fixed. Do not assert private
   implementation details, commit IDs, fixture-specific constants, or an expected patch.
5. Run the generated test against the current buggy checkout and confirm that it fails for the
   described behavioral reason. A syntax, import, dependency, or environment error is not evidence.
6. Keep the test self-contained and directly executable with the requested Python command. Avoid
   network, wall-clock, random, database, browser, GPU, and external-service dependencies.
7. Before finishing, verify that no production file changed and report the test command and observed
   failing assertion. Do not change the assertion merely to make the buggy checkout pass.

The final response must name the generated test file, summarize the behavior it covers, and report
the observed command result. Never claim the production defect is fixed by a test-only change.
