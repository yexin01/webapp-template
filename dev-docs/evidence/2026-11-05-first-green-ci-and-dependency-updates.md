# Evidence: The First Green Remote CI, and the Dependabot Updates (2026-11-05)

| Field | Value |
|---|---|
| Scope | Every pull request open on 2026-11-05: #16 (the audit branch: CI fixes, the readiness audit fixes, the Resend integration) and the Dependabot pull requests #1 to #15, then the ones Dependabot opened as `main` moved (#17, #19 to #22) |
| Why | The owner asked on 2026-11-05: "Can you resolve all the pull request into the main?" |
| Method | Remote CI on each pull request's head and on `main` after each merge (GitHub Actions, read through the GitHub API); local `pnpm verify` runs; the peer-dependency ranges of the lint toolchain; merges with merge commits, so the commits the evidence records cite stay reachable |
| Environment | Local: Linux (4 CPUs), Node 22.23.4, pnpm 9.15.0, uv 0.11.32, PostgreSQL from Docker. Remote: GitHub-hosted runners, Node 24 |
| Integration mode | `remote CI`, `local` and `mocked`. **Nothing is `sandbox` or `live`.** No provider was contacted |
| Remote actions | Under the owner's request of 2026-11-05: pushes to `claude/template-readiness-audit`; pull requests #18 and #23 opened; #16, #18 and #23 merged into `main` with merge commits after green CI. Two branches were pushed and never used for a pull request (`claude/dependency-updates`, `claude/deps-verified`); a force-push was refused by this session's permissions, and they were left in place. No deployment, release or provider call |

## Labels

- `remote CI`: a GitHub Actions run on a commit. It proves what a runner did with that commit, never a provider's behavior.
- `local`: a command run on one machine against local services.
- `NOT RUN`: a check exists and was not run. `NOT VERIFIED`: no sufficient evidence exists. Neither is PASS.

## Remote CI

| Pull request | Head | Pull request CI | `main` after the merge |
|---|---|---|---|
| #16 the audit branch | `18934b4` | Every check passed: API, API on Supabase's image, Web, deployment configuration, secret scan, CodeQL (three languages and its alert check) | `4d4a199`: CI and CodeQL passed |
| #18 the Dependabot updates | `9108c35` | Every check passed, the Dependabot configuration check included | `90efc4e`: CI and CodeQL passed |
| #23 the second round | `69ff0ae` | Every check passed | `94669e8`: CodeQL passed; CI was still running when this record was written |

The first green run of every CI job in this repository. Three failures came up on the way, each fixed in its pull request:

1. **CodeQL**, on #16 at `ef36169`: one new high alert, "Incomplete URL substring sanitization" (`py/incomplete-url-substring-sanitization`), at `apps/api/tests/unit/test_email.py:143`, an assertion `"example.com" in details`. A test, not a sanitizer; the assertion now compares the exact lines (`5f70eb5`).
2. **The generator check** (`pnpm test:new-module`), on #16 at `5f70eb5`, running on CI for the first time: "got no result line", while every generated module's 25 web tests had passed. With `CI` set, vitest colors its output without a terminal, and the result-line patterns no longer matched. `scripts/lib/steps.mjs` reads result lines without ANSI escape sequences, with a test holding the bytes the runner printed (`18934b4`). Locally `CI` is unset, which is why `pnpm verify` had passed.
3. **The install-script policy**, on #23 at `ef608f4`: vercel 65.9.0 brings koffi 3.3.3 (through `@vercel/cli-auth`), which declares an install script. The CLI loads koffi only to keep a login's credentials in a hardware key, and `vercel build` passed on the same run with the script blocked; koffi is now in the test's `BLOCKED` list with that reason (`69ff0ae`).

## Local runs

| Tree | Command | Result | Label |
|---|---|---|---|
| `f81b9c5` (the tree #18 merged, as `10dbdfe`) | `pnpm verify` | PASS: 23 steps passed; `secrets` SKIPPED (gitleaks is not installed; the pre-commit hook scanned every commit with the pinned gitleaks image) | `local`, `mocked` |
| The same updates with `@sentry/nextjs` 11.10.0 | `pnpm verify` | FAIL at `licenses`: `lightningcss` (MPL-2.0) is in the production tree; `e2e` and `secrets` did not run | `local` |
| `69ff0ae`, the API tests against `postgres:17.11-alpine` | `pnpm verify --job api --job web --skip new-module` | PASS: 22 steps; the generator check was left to CI, where it passed | `local`, `mocked` |

## The Dependabot pull requests

| Pull request | Update | Outcome |
|---|---|---|
| #2, #9, #10, #11 | pnpm/action-setup 6.1.0, actions/upload-artifact 7.0.2, astral-sh/setup-uv 10.3.3, actions/checkout 7.0.1 | In #18; verified by its CI only (they run on GitHub). Closed by Dependabot |
| #8, #19 | github/codeql-action 4.39.1 | In #18, `init` and `analyze` together. Closed by Dependabot |
| #3 to #7 | stripe 16.0.1, pytest 9.2.0, pydantic-settings 2.16.2, fastapi 0.162.0, svix 2.9.1 | In #18; the lock written by the local uv keeps `revision = 3` (Dependabot's had 5). Closed by Dependabot |
| #12, #20, #22 | The npm minor-and-patch group | In #18 and #23, **except `@sentry/nextjs` 11.10.0**: it adds `@sentry/cloudflare`, whose optional peer `vite` puts `lightningcss` (MPL-2.0) in the production dependency tree, which the license check (SEC-10) refuses. Accepting that license is the owner's review |
| #21 | postgres 17.11-alpine | In #23, with both CI service images |
| #1 | postgres 18.6-alpine | Not merged: Supabase and the CI images run 17, and a 17 data directory does not start on 18. `.github/dependabot.yml` now leaves the image's major to a person |
| #15 | @types/node 26 | Not merged: the apps run Node 24 (`.nvmrc`). Its major is now left to a person too |
| #13 | typescript 7.0.2 | **Open, the owner's call**: typescript-eslint 8.71.2 supports TypeScript `>=4.8.4 <6.1.0` |
| #14 | eslint 10 | **Open, the owner's call**: eslint-plugin-react, eslint-plugin-import and eslint-plugin-jsx-a11y declare ESLint 9 at most, and the lint configuration is the owner's to change |
| #17 | stripe 16.1.0 | **Open, the owner's call**: the release changes the Stripe API version the SDK pins (to `2026-10-28.endive`), and the adapter sends the SDK's version. A billing change (§17 rule 5); run `pnpm test:stripe-sandbox` against it first |

## Not run

`pnpm verify:sandbox`, the Stripe sandbox suite, `proofs/dec-001/run.sh`, and every check against a provider: NOT RUN. The code scanning alerts on `main` were not read; only the one alert #16 introduced was.
