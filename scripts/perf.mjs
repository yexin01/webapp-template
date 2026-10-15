#!/usr/bin/env node
/**
 * Runs a k6 scenario from perf/k6/scenarios.js in Docker against the LOCAL
 * perf server (pnpm perf:server). Raw results go to perf/results/ (git-ignored).
 *
 *   SCENARIO=baseline pnpm perf:run
 *   SCENARIO=stress MAX_RATE=100 STEP=20s pnpm perf:run
 *
 * BASE_URL must point at a local server unless an owner-approved run plan
 * says otherwise (spec §15.2). A deployed target also needs PERF_USERS_FILE
 * (synthetic users' session tokens) or, for webhooks, PERF_WEBHOOK_SECRET, and
 * a protected preview needs VERCEL_AUTOMATION_BYPASS_SECRET; see
 * perf/run-plan-template.md. None of these values is printed or put on a command line.
 *
 * Needs Docker Desktop: k6 reaches the perf server, which listens on 127.0.0.1,
 * through host.docker.internal. On Linux, see perf/README.md.
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const K6_IMAGE = "grafana/k6:1.3.0";
const PASSTHROUGH = [
  "SCENARIO",
  "DURATION",
  "RATE",
  "MAX_RATE",
  "MAX_VUS",
  "STEP",
  "SPIKE_RATE",
  "USERS",
  "ORGS",
  "SEED",
  "WEBHOOK_EVENTS",
  "WEBHOOK_VUS",
  "DUPLICATES",
  "EVENTS_PER_CUSTOMER",
  "INCLUDE_LLM",
  "LABEL",
];
const USERS_FILE_IN_CONTAINER = "/secrets/perf-users.json";

function fail(message) {
  console.error(message);
  process.exit(2);
}

const baseUrl = process.env.BASE_URL ?? "http://host.docker.internal:8001";
const parsedUrl = new URL(baseUrl);
// Credentials in the URL would land on the docker command line and in the results.
if (parsedUrl.username || parsedUrl.password) fail("BASE_URL must not contain a user name or password.");
const host = parsedUrl.hostname;
const local = ["host.docker.internal", "localhost", "127.0.0.1"].includes(host);
if (!local && process.env.PERF_ALLOW_REMOTE !== "1") {
  fail(`Refusing to load-test ${host}: only local targets are allowed without an approved run plan.`);
}

const scenario = process.env.SCENARIO ?? "baseline";
const usersFile = process.env.PERF_USERS_FILE ? path.resolve(process.env.PERF_USERS_FILE) : null;
if (!local && scenario !== "webhooks" && !usersFile) {
  fail(`${host} needs PERF_USERS_FILE: only the local perf server accepts the built-in synthetic tokens. See perf/run-plan-template.md.`);
}
if (!local && scenario === "webhooks" && !process.env.PERF_WEBHOOK_SECRET) {
  fail(`${host} needs PERF_WEBHOOK_SECRET (its test-only STRIPE_WEBHOOK_SECRET). See perf/run-plan-template.md.`);
}

mkdirSync(path.join(root, "perf/results"), { recursive: true });
const env = ["-e", `BASE_URL=${baseUrl}`];
for (const name of PASSTHROUGH) if (process.env[name]) env.push("-e", `${name}=${process.env[name]}`);
const mounts = [];
if (usersFile) {
  if (!statSync(usersFile, { throwIfNoEntry: false })?.isFile()) fail("PERF_USERS_FILE is not a readable file.");
  // It holds bearer tokens, so it must never land in the working tree. Real
  // paths, so a symbolic link or a differently cased path cannot slip inside.
  const relative = path.relative(realpathSync.native(root), realpathSync.native(usersFile));
  if (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) {
    fail("Keep PERF_USERS_FILE outside the repository: it holds session tokens.");
  }
  mounts.push("-v", `${usersFile}:${USERS_FILE_IN_CONTAINER}:ro`);
  env.push("-e", `PERF_USERS_FILE=${USERS_FILE_IN_CONTAINER}`);
}
// Names only: Docker copies the values from this process's environment.
for (const name of ["PERF_WEBHOOK_SECRET", "VERCEL_AUTOMATION_BYPASS_SECRET"]) {
  if (process.env[name]) env.push("-e", name);
}

const result = spawnSync(
  "docker",
  [
    "run",
    "--rm",
    "-v",
    `${path.join(root, "perf/k6")}:/scripts:ro`,
    "-v",
    `${path.join(root, "perf/results")}:/results`,
    ...mounts,
    ...env,
    K6_IMAGE,
    "run",
    "--quiet",
    "/scripts/scenarios.js",
  ],
  { stdio: "inherit" },
);
process.exit(result.status ?? 1);
