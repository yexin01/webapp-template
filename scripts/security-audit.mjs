#!/usr/bin/env node
// Dependency vulnerability audit for both ecosystems (SEC-04). A clean result
// is evidence of these checks only, not proof that no vulnerabilities exist.
import { spawnSync } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const steps = [
  ["pnpm", ["audit", "--prod", "--audit-level", "high"], root],
  ["sh", [path.join(root, "scripts/pip-audit-locked.sh")], root],
];
let failed = false;
for (const [cmd, args, cwd] of steps) {
  console.log(`\n$ ${cmd} ${args.join(" ")}`);
  const result = spawnSync(cmd, args, { cwd, stdio: "inherit" });
  if (result.status !== 0) failed = true;
}
process.exit(failed ? 1 : 0);
