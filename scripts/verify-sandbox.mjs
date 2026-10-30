#!/usr/bin/env node
/**
 * The owner's one command after pasting sandbox keys into .env: runs the checks that need provider accounts
 * (the setup check, the Stripe sandbox suite, the sandbox end-to-end run), each only when its keys are there,
 * and ends with an evidence record (spec section 20) to paste under dev-docs/evidence/.
 *
 *   pnpm verify:sandbox                     run what the .env allows; skip, with the reason, what it does not
 *   pnpm verify:sandbox --send-test-events  also send one PostHog event and one Sentry issue (sandbox projects)
 *   pnpm verify:sandbox --skip e2e          leave a part out (setup, stripe, e2e)
 *
 * With nothing configured every part is skipped and the exit status is 0: nothing is verified, and the record says so.
 * Exit status 1 when a part failed or could not run. `pnpm verify` is the account-free counterpart.
 */
import { main } from "./lib/verify-sandbox.mjs";

process.exitCode = await main(process.argv.slice(2));
