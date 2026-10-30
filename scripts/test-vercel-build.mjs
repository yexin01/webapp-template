#!/usr/bin/env node
/**
 * Deployment configuration check (spec 16.1): builds the root vercel.json
 * locally and checks the output. No deployment, no Vercel account, no secrets.
 *
 *   pnpm test:vercel-build           # needs pnpm install, uv, and network access to npm and PyPI
 *   pnpm test:vercel-build --keep    # keep the throwaway copy and the build log
 *
 * What it does:
 * 1. Copies the working tree (tracked files and untracked files that git does
 *    not ignore) into a throwaway directory, so nothing is written to the repo.
 * 2. Runs the Vercel CLI pinned in package.json (`vercel build`, the same
 *    builders Vercel runs, installing dependencies from npm and PyPI) with an
 *    empty global configuration, a local project file (framework "services"),
 *    telemetry off, and no token, organization, or project in the environment.
 * 3. Builds the web service with generated canary values for the server-only
 *    variables, then requires check-bundle-secrets to compare them against the
 *    browser bundle (under Services every project variable reaches the web
 *    build, so this is the environment the scan is meant for).
 * 4. Runs scripts/check_vercel_build.py on the output: both services, routing
 *    order, crons against the job registry, the function region, a Python 3.12
 *    x86-64 Linux bundle without tests, and the size limit.
 *
 * This is local packaging evidence. It does not prove that Vercel accepts the
 * configuration (Services is Beta and needs the project's Framework Preset set
 * to Services), nor how the platform routes, runs, or bills it.
 *
 * VERCEL_BUILD_PYTHON: an interpreter with the API's runtime dependencies
 * (CI uses the production-only environment). Default: `uv run python` in apps/api.
 */
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { copyFileSync, existsSync, lstatSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const keep = process.argv.includes("--keep");
const cli = path.join(root, "node_modules", ".bin", "vercel");
const BUILD_TIMEOUT_MS = 30 * 60 * 1000;

class StepError extends Error {}

function fail(message) {
  throw new StepError(message);
}

function run(command, args, options) {
  const result = spawnSync(command, args, { encoding: "utf8", maxBuffer: 256 * 1024 * 1024, ...options });
  if (result.error) fail(`could not run ${command}: ${result.error.message}`);
  return result;
}

/** Tracked files, plus untracked files git does not ignore, as they are in the working tree now. */
function workingTreeFiles() {
  const listed = run("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], { cwd: root });
  if (listed.status !== 0) fail(`git ls-files failed: ${listed.stderr}`);
  return listed.stdout.split("\0").filter(Boolean);
}

function exportTree(destination) {
  let count = 0;
  for (const file of workingTreeFiles()) {
    const from = path.join(root, file);
    if (!existsSync(from) || !lstatSync(from).isFile()) continue; // deleted in the working tree, or a symlink
    mkdirSync(path.dirname(path.join(destination, file)), { recursive: true });
    copyFileSync(from, path.join(destination, file));
    count += 1;
  }
  return count;
}

/** Random stand-ins for the server-only variables. Built at run time, never committed, and invalid anywhere. */
function canaries() {
  const hex = (bytes) => randomBytes(bytes).toString("hex");
  return {
    CLERK_SECRET_KEY: `${"sk"}_${"test"}_${hex(16)}`,
    CLERK_WEBHOOK_SIGNING_SECRET: `${"whsec"}_${hex(16)}`,
    STRIPE_SECRET_KEY: `${"sk"}_${"test"}_${hex(16)}`,
    STRIPE_WEBHOOK_SECRET: `${"whsec"}_${hex(16)}`,
    DATABASE_URL: `postgresql://app_login.canary:${hex(12)}@pooler.invalid:6543/postgres`,
    MIGRATION_DATABASE_URL: `postgresql://postgres.canary:${hex(12)}@pooler.invalid:5432/postgres`,
    API_DB_PASSWORD: hex(12),
    LLM_API_KEY: hex(16),
    CRON_SECRET: hex(20),
  };
}

function buildEnvironment() {
  const env = { ...process.env };
  // The CLI reads the first three from the environment whatever -Q says. A real SENTRY_AUTH_TOKEN would upload
  // source maps, and CLERK_JWT_KEY must be a PEM public key: neither is set, so a developer's shell cannot reach out.
  for (const name of ["VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID", "SENTRY_AUTH_TOKEN", "CLERK_JWT_KEY"]) {
    delete env[name];
  }
  return {
    ...env,
    ...canaries(),
    APP_ENV: "preview",
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:
      env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ||
      `pk_test_${Buffer.from("placeholder.clerk.accounts.dev$").toString("base64")}`,
    VERCEL_TELEMETRY_DISABLED: "1",
    NEXT_TELEMETRY_DISABLED: "1",
    NO_UPDATE_NOTIFIER: "1",
  };
}

const work = mkdtempSync(path.join(tmpdir(), "vercel-build-"));
const repo = path.join(work, "repo");
const emptyGlobalConfig = path.join(work, "no-credentials");
mkdirSync(repo);
mkdirSync(emptyGlobalConfig);
let ok = false;
try {
  if (!existsSync(cli)) fail("the pinned Vercel CLI is not installed. Run `pnpm install`.");
  const files = exportTree(repo);
  console.log(`Copied ${files} files to ${repo}`);
  if (!existsSync(path.join(repo, "vercel.json"))) fail("the repository has no root vercel.json");
  mkdirSync(path.join(repo, ".vercel"));
  // Local project settings, so `vercel build` never pulls settings from an account.
  writeFileSync(
    path.join(repo, ".vercel", "project.json"),
    JSON.stringify({
      projectId: "prj_local_check",
      orgId: "team_local_check",
      settings: {
        framework: "services",
        rootDirectory: null,
        installCommand: null,
        buildCommand: null,
        outputDirectory: null,
        devCommand: null,
        nodeVersion: "24.x",
      },
    }),
  );

  const env = buildEnvironment();
  console.log(`\n$ vercel build (${run(cli, ["--version"], { env }).stdout.trim().split("\n").pop()})`);
  const log = path.join(work, "vercel-build.log");
  const build = run(cli, ["build", "-Q", emptyGlobalConfig, "--non-interactive"], {
    cwd: repo,
    env,
    timeout: BUILD_TIMEOUT_MS,
  });
  writeFileSync(log, `${build.stdout}\n${build.stderr}`);
  if (build.status !== 0) {
    console.error(`${build.stdout}\n${build.stderr}`.trim().split("\n").slice(-60).join("\n"));
    fail(`vercel build failed (exit ${build.status}). Full log: ${log}`);
  }
  console.log("vercel build: ok");

  console.log("\n$ node scripts/check-bundle-secrets.mjs --require-values");
  const scan = run("node", ["scripts/check-bundle-secrets.mjs", "--require-values"], { cwd: repo, env });
  process.stdout.write(scan.stdout);
  process.stderr.write(scan.stderr);
  if (scan.status !== 0) fail("the browser bundle check failed");

  const python = (process.env.VERCEL_BUILD_PYTHON || "").trim()
    ? [process.env.VERCEL_BUILD_PYTHON.trim()]
    : ["uv", "run", "--project", path.join(root, "apps", "api"), "--frozen", "python"];
  console.log("\n$ python scripts/check_vercel_build.py check");
  const checked = run(
    python[0],
    [
      ...python.slice(1),
      path.join(root, "scripts", "check_vercel_build.py"),
      "check",
      "--root",
      repo,
      "--output",
      path.join(repo, ".vercel", "output"),
    ],
    { cwd: root, env: { ...process.env, APP_ENV: "test" } },
  );
  process.stdout.write(checked.stdout);
  process.stderr.write(checked.stderr);
  if (checked.status !== 0) fail("the build output checks failed");
  ok = true;
  console.log("\nDeployment configuration: ok (local packaging evidence only; nothing was deployed).");
} catch (error) {
  if (!(error instanceof StepError)) throw error;
  console.error(`\ntest-vercel-build: ${error.message}`);
  process.exitCode = 1;
} finally {
  if (ok && !keep) rmSync(work, { recursive: true, force: true });
  else console.log(`\nKept ${work} (build log: vercel-build.log). Remove it when done.`);
}
