#!/usr/bin/env node
// Runs the Python API and the Next.js app together for local development.
import { spawn } from "node:child_process";

const children = [
  spawn("pnpm", ["dev:api"], { stdio: "inherit", shell: false }),
  spawn("pnpm", ["dev:web"], { stdio: "inherit", shell: false }),
];

function shutdown(code = 0) {
  for (const child of children) child.kill("SIGTERM");
  process.exit(code);
}

for (const child of children) child.on("exit", (code) => shutdown(code ?? 0));
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
