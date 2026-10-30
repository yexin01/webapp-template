#!/usr/bin/env node
/**
 * Account-free production build plus the bundle secret scan (ENV-07): what CI
 * runs, and what a fresh clone can run before it has any Clerk keys.
 *
 * The build gets a placeholder Clerk publishable key (the one .env.example ships)
 * and a synthetic secret key made up for this run, so the scan proves the one
 * server-only value the web build can see stays out of the bundle. Telemetry is
 * off and APP_ENV is local whatever .env says. Nothing is read from or written to
 * .env, and no value is printed. The result is a scan-only build, not a
 * deployable one: `pnpm build` is the build that needs your own Clerk keys.
 *
 *   pnpm build:check
 */
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";

import { PLACEHOLDER_PUBLISHABLE_KEY } from "./lib/clerk.mjs";

const root = path.resolve(import.meta.dirname, "..");
const env = {
  ...process.env,
  APP_ENV: "local",
  ANALYTICS_MODE: "disabled",
  ERROR_REPORTING_MODE: "disabled",
  CLERK_JWT_KEY: "",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY,
  CLERK_SECRET_KEY: `sk_test_${randomBytes(24).toString("hex")}`,
};

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
