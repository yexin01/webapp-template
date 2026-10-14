#!/usr/bin/env node
/**
 * Runs a k6 scenario from perf/k6/scenarios.js in Docker against the LOCAL
 * perf server (pnpm perf:server). Raw results go to perf/results/ (git-ignored).
 *
 *   SCENARIO=baseline pnpm perf:run
 *   SCENARIO=stress MAX_RATE=100 STEP=20s pnpm perf:run
 *
 * BASE_URL must point at a local server unless an owner-approved run plan
 * says otherwise (spec §15.2).
 */
import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
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
  "WEBHOOK_EVENTS",
  "WEBHOOK_VUS",
  "DUPLICATES",
];

const baseUrl = process.env.BASE_URL ?? "http://host.docker.internal:8001";
const host = new URL(baseUrl).hostname;
if (!["host.docker.internal", "localhost", "127.0.0.1"].includes(host) && process.env.PERF_ALLOW_REMOTE !== "1") {
  console.error(`Refusing to load-test ${host}: only local targets are allowed without an approved run plan.`);
  process.exit(2);
}

mkdirSync(path.join(root, "perf/results"), { recursive: true });
const env = ["-e", `BASE_URL=${baseUrl}`];
for (const name of PASSTHROUGH) if (process.env[name]) env.push("-e", `${name}=${process.env[name]}`);

const result = spawnSync(
  "docker",
  [
    "run",
    "--rm",
    "-v",
    `${path.join(root, "perf/k6")}:/scripts:ro`,
    "-v",
    `${path.join(root, "perf/results")}:/results`,
    ...env,
    K6_IMAGE,
    "run",
    "--quiet",
    "/scripts/scenarios.js",
  ],
  { stdio: "inherit" },
);
process.exit(result.status ?? 1);
