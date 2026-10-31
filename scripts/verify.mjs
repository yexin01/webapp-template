#!/usr/bin/env node
/**
 * One command for "is my change done, and will CI agree?": runs the checks of CI's api, web and secrets jobs,
 * in CI's order, and ends with a PASS/FAIL table (commit, working-tree state, each step's result and time).
 * Exit status 0 only when no step failed and one passed. The deployment configuration is `pnpm test:vercel-build`
 * and the Supabase image job is `pnpm test:supabase`; neither is part of the default run.
 *
 *   pnpm verify                  CI's api and web jobs, plus the secret scan if gitleaks is installed
 *   pnpm verify:quick            lint, format, types, and the API and web tests
 *   pnpm verify --no-db          allow the database tests not to run (otherwise an unreachable PostgreSQL is an error)
 *   pnpm verify --job api        one CI job (CI runs `node scripts/verify.mjs --job api` and `--job web`)
 *   pnpm verify --list           the steps, and why any would be skipped
 *
 * `pnpm verify --help` has the rest. The steps and their order are scripts/lib/verify.mjs.
 */
import { main } from "./lib/verify.mjs";

process.exitCode = await main(process.argv.slice(2));
