# AGENTS.md

The authoritative specification for this repository is
[`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md).
Read it before making changes. Do not restate its requirements in other files; link to it.

## Critical rules (full text in §17 of the specification)

1. **Never** push, publish, release, deploy, or modify remote repository or service state without explicit authorization from the owner. Local commits are allowed.
2. **Never** expose, commit, print, log, or transmit secrets, credentials, or personal data.
3. **Never** resolve an open decision (§3) on the owner's behalf or implement scope it blocks. Ask.
4. **Never** fabricate test results, measurements, security findings, or deployment evidence. Label integration evidence as `mocked`, `sandbox`, or `live`.
5. **Never** weaken tests or disable required checks to get a passing result.
6. Preserve unrelated work. Ask before destructive operations.

## Where things are

- Open decisions register: §3
- Architecture and boundaries: §4
- Implementation phases and current status: §18, §23
- Quality gates and evidence statuses: §19, §20
- Requirements traceability matrix: §21
