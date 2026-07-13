# Security policy

## Supported versions

Until the first stable release, security fixes target the latest commit on `main` and the current
release-candidate branch only.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or open a private security advisory for this
repository. Do not disclose exploitable details, credentials, private fixtures or unredacted Agent
traces in a public issue.

Include the affected commit, reproduction steps, impact and any proposed mitigation. Maintainers
will acknowledge a complete report within seven days when possible.

## Credential and execution boundary

- Real Provider secrets must come from explicitly allowlisted environment variables.
- Secrets, raw credentials and unsafe logs must never be committed or included in replay bundles.
- CI and default demos must not make paid model calls.
- A real run requires an explicit confirmation flag, cost ceiling and Agent-run ceiling.
- Suspected credential exposure should be handled by revoking the credential before filing a report.
