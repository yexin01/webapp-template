#!/usr/bin/env node
/**
 * Account-free production build plus the bundle secret scan (ENV-07): what CI
 * runs, and what a fresh clone can run before it has any Clerk keys.
 *
 * The build's environment is scripts/lib/build-check-env.mjs: placeholder and
 * synthetic Clerk keys, telemetry off, APP_ENV local, and no .env read by the
 * build (SKIP_ROOT_ENV_FILE), so it does not depend on the machine. The scan
 * does read the values in .env, to look for them in the output. Nothing is
 * written to .env and no value is printed. The result is a scan-only build,
 * not a deployable one: `pnpm build` is the build that needs your own Clerk keys.
 *
 *   pnpm build:check
 */
import { spawnSync } from "node:child_process";
import path from "node:path";

import { buildCheckEnv } from "./lib/build-check-env.mjs";

const root = path.resolve(import.meta.dirname, "..");
const env = buildCheckEnv();

function run(label, command, args) {
  console.log(`build:check: ${label}`);
  const result = spawnSync(command, args, { cwd: root, env, stdio: "inherit", shell: command === "pnpm" && process.platform === "win32" });
  if (result.status !== 0) {
    console.error(`build:check: failed at "${label}"${result.error ? ` (${result.error.message})` : ""}.`);
    process.exit(result.status ?? 1);
  }
}

console.log("build:check: a scan-only build with placeholder Clerk keys and no telemetry; it is not deployable.");
run("pnpm --filter web build", "pnpm", ["--filter", "web", "build"]);
run("bundle scan self-test", process.execPath, ["scripts/check-bundle-secrets.mjs", "--self-test"]);
run("bundle scan", process.execPath, ["scripts/check-bundle-secrets.mjs"]);
console.log("build:check: passed. Run `pnpm build` with your own Clerk keys for a deployable build.");
