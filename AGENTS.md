# Project Execution Rules

- Use local, zero-metered-cost execution. Paid APIs, hosted CI runners, and cloud build services
  are forbidden.
- Do not add GitHub Actions workflows. Run Python, Dashboard, build, and clean-room checks locally.
- Preserve `FAIL`, infrastructure `INVALID`, no-gain, and regression results as first-class evidence.
- Bind evaluation claims to immutable Skill, DatasetVersion, Runner, Agent Runtime, config, and
  artifact hashes.
- Apply generated Skill or system changes only through proposal, review, explicit approval, and
  immutable publication.
- Prefer existing components and the smallest change that satisfies the frozen acceptance gate.
