#!/usr/bin/env node
/**
 * One-command setup check (the "doctor"): the configuration preflight of `pnpm dev`, then the
 * integration checks of the /integrations page without signing in
 * (`python -m app.cli diagnose`). It ends with an evidence block to paste into
 * dev-docs/evidence/ and the manual steps still needed.
 *
 *   pnpm check:setup [--send-test-events]
 *
 * (Not `pnpm doctor`: that is a built-in pnpm command, and it wins over a script of the same name.)
 *
 * --send-test-events also sends one PostHog event and one Sentry issue (sandbox
 * projects only). Refuses APP_ENV=production. Exit status 1 if a check failed.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { parseArgs } from "node:util";

import { prepareLocalEnv } from "./lib/local-env.mjs";

const root = path.resolve(import.meta.dirname, "..");

let sendTestEvents;
try {
  ({
    values: { "send-test-events": sendTestEvents },
  } = parseArgs({ options: { "send-test-events": { type: "boolean", default: false } } }));
} catch (error) {
  console.error(`check:setup: ${error.message}\nUsage: pnpm check:setup [--send-test-events]`);
  process.exit(2);
}

const prepared = await prepareLocalEnv({ root, command: "check:setup" });
if (!prepared.ok) process.exit(1);

const args = ["run", "python", "-m", "app.cli", "diagnose", ...(sendTestEvents ? ["--send-test-events"] : [])];
const child = spawn("uv", args, {
  cwd: path.join(root, "apps/api"),
  env: { ...process.env, ...prepared.apiEnv },
  stdio: "inherit",
});
child.on("error", (error) => {
  console.error(`check:setup: could not run uv: ${error.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => process.exit(signal ? 1 : (code ?? 1)));
