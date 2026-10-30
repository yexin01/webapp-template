#!/usr/bin/env node
// Runs the Python API and the Next.js app together for local development.
// macOS and Linux only; on Windows, use WSL.
//
// Before it starts anything it checks the configuration (scripts/lib/preflight.mjs)
// and stops with a short list of problems. With BILLING_MODE=sandbox, an sk_test_ key
// and the Stripe CLI installed, it also supplies the Stripe webhook signing secret to
// the API (this run only; never written to .env) and forwards webhooks to it. Once the
// servers are up it asks the API for /api/v1/health: uvicorn's reloader stays alive
// when its worker dies at import, so a dead API is otherwise silent.
//
// Each server starts in its own process group, and stopping signals the whole
// group, so pnpm, the shell, uvicorn's reloader and worker, and next dev's
// workers all stop together. Stopping happens on SIGINT, SIGTERM, or SIGHUP,
// or as soon as either server exits: every group gets SIGTERM, then SIGKILL
// after GRACE_MS. A second signal skips the grace period. The exit status is
// non-zero when a signal stopped the servers or a server exited on its own.
import { spawn } from "node:child_process";
import { constants } from "node:os";
import path from "node:path";

import { HEALTH_URL, lastErrorLine, waitForHealth } from "./lib/dev-health.mjs";
import { startListener } from "./lib/dev-stripe.mjs";
import { prepareLocalEnv } from "./lib/local-env.mjs";

const root = path.resolve(import.meta.dirname, "..");
const COMMANDS = [
  ["pnpm", ["dev:api"]],
  ["pnpm", ["dev:web"]],
];
const GRACE_MS = 5000;
const API_START_TIMEOUT_MS = 60_000;

if (process.platform === "win32") {
  // No process groups to stop, and spawning the pnpm.cmd shim needs a shell.
  console.error("dev: needs macOS or Linux (on Windows, use WSL), or run `pnpm dev:api` and `pnpm dev:web` separately.");
  process.exit(1);
}

let stopping = false;
let killed = false;
let exitCode = 0;
const children = [];

/** True while any process in the child's group is alive. */
function alive(child) {
  if (child.pid === undefined) return false;
  try {
    process.kill(-child.pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

function signalAll(signal) {
  for (const child of children) {
    if (!alive(child)) continue;
    try {
      process.kill(-child.pid, signal);
    } catch (error) {
      if (error.code !== "ESRCH") console.error(`dev: could not send ${signal} to ${child.pid}: ${error.message}`);
    }
  }
}

// Sends SIGKILL once. A group whose processes have all just died can still
// answer EPERM on macOS until they are reaped, so a second round only adds noise.
function killAll() {
  if (killed) return;
  killed = true;
  signalAll("SIGKILL");
}

function stop(code) {
  if (stopping) return;
  stopping = true;
  exitCode = code;
  signalAll("SIGTERM");
  const deadline = Date.now() + GRACE_MS;
  const poll = setInterval(() => {
    if (!children.some(alive)) {
      clearInterval(poll);
      process.exit(exitCode);
    }
    if (Date.now() >= deadline) {
      console.error(`dev: servers still running after ${GRACE_MS} ms; sending SIGKILL.`);
      killAll();
      clearInterval(poll);
      process.exit(exitCode);
    }
  }, 100);
}

// Registered before anything starts: a signal that arrives while the servers
// are being spawned is held until they are in `children`, then stops them.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    if (stopping) {
      killAll();
      process.exit(exitCode);
    }
    stop(128 + constants.signals[signal]);
  });
}

// Last resort if this script fails unexpectedly: never leave a server behind.
process.on("exit", killAll);

/** Spawns a server in its own process group. `apiEnv` is for the API only. */
function start(command, args, { env, pipeStderr = false } = {}) {
  // A detached child leads a new process group whose ID is its PID. It is not
  // in the terminal's foreground group, so it must not read the terminal:
  // stdin is closed. Ctrl-C reaches this process, which stops the groups.
  const child = spawn(command, args, {
    stdio: ["ignore", "inherit", pipeStderr ? "pipe" : "inherit"],
    detached: true,
    env,
  });
  child.on("error", (error) => {
    console.error(`dev: could not start ${command} ${args.join(" ")}: ${error.message}`);
    stop(1);
  });
  child.on("exit", (code, signal) => {
    if (stopping) return;
    console.error(`dev: ${args.join(" ")} exited (${signal ?? `code ${code}`}); stopping the other server.`);
    // A dev server never exits on its own when healthy, so even a clean exit fails the run.
    stop(signal ? 128 + (constants.signals[signal] ?? 0) : code || 1);
  });
  children.push(child);
  return child;
}

function startStripeListener({ apiKey, secret }) {
  const child = startListener({
    apiKey,
    secret,
    onLine: (line) => console.log(`stripe: ${line}`),
    onExit: ({ error, code, signal }) => {
      if (stopping) return;
      console.error(
        error
          ? `dev: could not start the Stripe CLI: ${error.message}`
          : `dev: the Stripe CLI listener exited (${signal ?? `code ${code}`}); Stripe webhooks no longer reach this machine. The servers keep running.`,
      );
    },
  });
  children.push(child);
}

async function main() {
  const prepared = await prepareLocalEnv({ root, command: "dev", requireClerk: true });
  if (stopping) return;
  if (!prepared.ok) process.exit(1);

  let apiErrors = "";
  const [apiCommand, apiArgs] = COMMANDS[0];
  const api = start(apiCommand, apiArgs, { env: { ...process.env, ...prepared.apiEnv }, pipeStderr: true });
  api.stderr.on("data", (chunk) => {
    process.stderr.write(chunk);
    apiErrors = (apiErrors + chunk).slice(-8000);
  });
  for (const [command, args] of COMMANDS.slice(1)) start(command, args);

  const health = await waitForHealth({
    timeoutMs: API_START_TIMEOUT_MS,
    isStopped: () => stopping,
    onSlow: () => console.error("dev: still waiting for the API to answer..."),
  });
  if (stopping) return;
  if (!health.ok) {
    console.error(`dev: the API did not answer ${HEALTH_URL} within ${API_START_TIMEOUT_MS / 1000} s, so it is not running.`);
    const line = lastErrorLine(apiErrors);
    if (line) console.error(`dev: the API's last error: ${line}`);
    console.error("dev: fix the cause (usually .env; the API does not reload when .env changes), then run pnpm dev again.");
    stop(1);
    return;
  }
  console.error("dev: the API is up. Web: http://localhost:3000");
  if (prepared.listen) {
    console.error("dev: forwarding Stripe webhooks to the API with the Stripe CLI (its signing secret is used for this run only).");
    startStripeListener(prepared.listen);
  }
}

main().catch((error) => {
  console.error(`dev: ${error.message}`);
  stop(1);
});
