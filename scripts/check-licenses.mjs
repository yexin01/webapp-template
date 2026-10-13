#!/usr/bin/env node
// SEC-10: production dependencies must use licenses compatible with
// distributing this Apache-2.0 template. Strong-copyleft and source-available
// licenses fail the check; unrecognized licenses are reported for review.
import { execFileSync } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const DENY = [/\bAGPL/i, /(^|[^L])GPL/i, /\bSSPL/i, /\bBUSL/i, /Commons Clause/i, /\bEUPL/i];

const findings = [];
const unknown = [];

function check(ecosystem, name, license) {
  if (!license || /unknown|UNLICENSED|see license/i.test(license)) unknown.push(`${ecosystem}: ${name}`);
  else if (DENY.some((re) => re.test(license)) && !/\bOR\b.*\b(MIT|Apache|BSD|ISC)/i.test(license))
    findings.push(`${ecosystem}: ${name} (${license})`);
}

// JavaScript (production dependencies only)
const js = JSON.parse(
  execFileSync("pnpm", ["licenses", "list", "--prod", "--json"], { cwd: root, encoding: "utf8" }),
);
for (const [license, packages] of Object.entries(js)) for (const p of packages) check("npm", p.name, license);

// Python (runtime dependencies only, resolved from the lockfile)
const apiDir = path.join(root, "apps/api");
const runtime = execFileSync("uv", ["export", "--frozen", "--no-dev", "--no-hashes", "--no-header", "--no-emit-project"], {
  cwd: apiDir,
  encoding: "utf8",
})
  .split("\n")
  .map((line) => line.trim().split(/[=<>;\s\[]/)[0])
  .filter((name) => name && !name.startsWith("#"));
const py = JSON.parse(
  execFileSync("uv", ["run", "--no-sync", "python", "-I", path.join(root, "scripts/python_licenses.py")], {
    cwd: apiDir,
    encoding: "utf8",
    input: runtime.join("\n"),
  }),
);
for (const { name, license } of py) check("pypi", name, license);

if (unknown.length) console.warn(`Licenses needing manual review:\n  ${unknown.join("\n  ")}`);
if (findings.length) {
  console.error(`Disallowed licenses:\n  ${findings.join("\n  ")}`);
  process.exit(1);
}
console.log(`License check passed (${Object.values(js).flat().length} npm, ${py.length} PyPI packages).`);
