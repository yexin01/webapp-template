#!/usr/bin/env node
// Dependency vulnerability audit for both ecosystems (SEC-04). A clean result
// is evidence of these checks only, not proof that no vulnerabilities exist.
//
// Severity: both blocking audits fail on an advisory of any severity.
// pip-audit has no severity filter, so the npm audit matches it rather than
// stopping at "high". CI runs the same two blocking audits.
//
// Scope (SEC-04 sets none; the difference is deliberate):
//   - npm, blocking: production dependencies, which ship in the web app. Checked
//     through the registry's bulk advisory endpoint (scripts/npm-audit-bulk.mjs):
//     pnpm 9's `pnpm audit` uses a legacy endpoint that is being retired.
//   - npm, report only: development dependencies. This tree is hundreds of
//     transitive lint, build, and test packages that never ship, and their
//     advisories often have no fix short of an override. Review the report;
//     it does not fail the run.
//   - PyPI, blocking: every version pinned in apps/api/uv.lock, runtime and
//     development (a small, directly pinned set).
import { spawnSync } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const steps = [
  { cmd: "node", args: [path.join(root, "scripts/npm-audit-bulk.mjs")], blocking: true },
  { cmd: "pnpm", args: ["audit", "--dev"], blocking: false },
  { cmd: "sh", args: [path.join(root, "scripts/pip-audit-locked.sh")], blocking: true },
];
let failed = false;
for (const { cmd, args, blocking } of steps) {
  console.log(`\n$ ${cmd} ${args.join(" ")}${blocking ? "" : "   (report only)"}`);
  const result = spawnSync(cmd, args, { cwd: root, stdio: "inherit" });
  if (result.status === 0) continue;
  if (blocking) failed = true;
  else console.log("Report only: review the advisories above; they do not fail this audit.");
}
process.exit(failed ? 1 : 0);
