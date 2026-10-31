#!/usr/bin/env node
/**
 * Account-free production build plus the bundle secret scan (ENV-07): what CI
 * runs, and what a fresh clone can run before it has any Clerk keys.
 *
 * The build's environment is scripts/lib/build-check-env.mjs: an allowlist of
 * machine variables plus placeholder and synthetic Clerk keys, telemetry off,
 * APP_ENV local, and no .env read by the build (SKIP_ROOT_ENV_FILE), so it does
 * not depend on the shell or on the machine's settings. The scan reads the
 * shell's values and those in .env as well, to look for them in the output.
 * Nothing is written to .env and no value is printed. The result is a scan-only
 * build, not a deployable one: `pnpm build` is the build that needs your own
 * Clerk keys.
 *
 *   pnpm build:check
 */
import { spawnSync } from "node:child_process";
import path from "node:path";

import { buildCheckEnv } from "./lib/build-check-env.mjs";

const root = path.resolve(import.meta.dirname, "..");
const buildEnv = buildCheckEnv();
// The scan compares the values it can see with the output: the build's own, the shell's, and .env's.
const scanEnv = { ...process.env, ...buildEnv };

function run(label, command, args, env) {
  console.log(`build:check: ${label}`);
  const result = spawnSync(command, args, { cwd: root, env, stdio: "inherit", shell: command === "pnpm" && process.platform === "win32" });
  if (result.status !== 0) {
    console.error(`build:check: failed at "${label}"${result.error ? ` (${result.error.message})` : ""}.`);
    if (env === buildEnv && (result.error || result.status === 127)) {
      console.error("build:check: the build runs with a minimal environment; add a variable your tools need to MACHINE_VARIABLES in scripts/lib/build-check-env.mjs.");
    }
    process.exit(result.status ?? 1);
  }
}

console.log("build:check: a scan-only build with placeholder Clerk keys and no telemetry; it is not deployable.");
run("pnpm --filter web build", "pnpm", ["--filter", "web", "build"], buildEnv);
run("bundle scan self-test", process.execPath, ["scripts/check-bundle-secrets.mjs", "--self-test"], scanEnv);
run("bundle scan", process.execPath, ["scripts/check-bundle-secrets.mjs"], scanEnv);
console.log("build:check: passed. Run `pnpm build` with your own Clerk keys for a deployable build.");
