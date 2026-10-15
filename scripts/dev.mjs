#!/usr/bin/env node
// Runs the Python API and the Next.js app together for local development.
// macOS and Linux only; on Windows, use WSL.
//
// Each server starts in its own process group, and stopping signals the whole
// group, so pnpm, the shell, uvicorn's reloader and worker, and next dev's
// workers all stop together. Stopping happens on SIGINT, SIGTERM, or SIGHUP,
// or as soon as either server exits: every group gets SIGTERM, then SIGKILL
// after GRACE_MS. A second signal skips the grace period. The exit status is
// non-zero when a signal stopped the servers or a server exited on its own.
import { spawn } from "node:child_process";
import { constants } from "node:os";

const COMMANDS = [
  ["pnpm", ["dev:api"]],
  ["pnpm", ["dev:web"]],
];
const GRACE_MS = 5000;

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

for (const [command, args] of COMMANDS) {
  // A detached child leads a new process group whose ID is its PID. It is not
  // in the terminal's foreground group, so it must not read the terminal:
  // stdin is closed. Ctrl-C reaches this process, which stops the groups.
  const child = spawn(command, args, { stdio: ["ignore", "inherit", "inherit"], detached: true });
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
}
