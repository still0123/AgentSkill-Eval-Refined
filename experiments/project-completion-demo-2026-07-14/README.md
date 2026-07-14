# Project completion demo evidence

This directory records the sanitized result of the zero-credential project
completion demo. The run used deterministic fixtures and did not invoke an
Agent, model API, Runner, network service, or paid provider.

The demo completed 72 logical Runs across 12 cases, two variants, and three
repeats with no invalid Runs. Its 980-file audit bundle verified successfully.
All reported performance values are simulated and are evidence of the platform
workflow only.

Raw manifests, reports, workspaces, and the audit tar remain local and ignored.
`result.sanitized.json` preserves the reproducibility hashes and aggregate
acceptance facts without absolute paths or credentials.
