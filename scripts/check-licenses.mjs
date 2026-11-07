#!/usr/bin/env node
// SEC-10: production dependencies must carry a license on the allowlist below,
// which is compatible with distributing this Apache-2.0 template (DEC-012).
// Anything else fails: unknown, unlisted, copyleft, and source-available
// licenses alike, unless the exact package and license are listed in the
// reviewed EXCEPTIONS with a reason.
//
//   node scripts/check-licenses.mjs              check npm and PyPI production dependencies
//   node scripts/check-licenses.mjs --self-test  check the license-expression evaluator
import { execFileSync } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");

/** Permissive licenses (SPDX identifiers). */
const ALLOWED = new Set([
  "0BSD",
  "Apache-2.0",
  "BlueOak-1.0.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "CC-BY-4.0", // attribution only; data packages such as caniuse-lite
  "CC0-1.0",
  "ISC",
  "MIT",
  "MIT-0",
  "PSF-2.0",
  "Python-2.0",
  "Unlicense",
  "Zlib",
]);

/**
 * SPDX license exceptions accepted after WITH. Each only grants extra
 * permissions on top of an allowed base license. Any other exception fails the
 * whole expression, so restrictions dressed up as exceptions (for example
 * "Apache-2.0 WITH Commons-Clause") never pass. Exceptions for copyleft bases
 * (Classpath-exception-2.0, GCC-exception-3.1, and so on) are deliberately
 * absent: their base licenses are not allowed, so such a package needs a
 * reviewed entry in EXCEPTIONS below. No current dependency uses WITH.
 */
const ALLOWED_WITH = new Set([
  "LLVM-exception", // Apache-2.0 WITH LLVM-exception: removes the attribution requirement for compiled output
]);

/**
 * Reviewed exceptions: one package (or package family) with one exact license
 * string each. A changed license fails again until it is reviewed.
 */
const EXCEPTIONS = [
  {
    ecosystem: "npm",
    name: /^sentry$/,
    license: "FSL-1.1-Apache-2.0",
    // Source-available (converts to Apache-2.0 after two years). It is the
    // Sentry CLI, pulled in by @sentry/nextjs's bundler plugin for source-map
    // upload. Build-time only: only @sentry/nextjs/config (next.config.ts)
    // loads it, and it is not in the browser bundle or the traced server
    // output. Owner review pending.
    reason: "build-time only, owner review pending",
  },
  {
    ecosystem: "npm",
    name: /^@img\/sharp-libvips-[a-z0-9-]+$/,
    license: "LGPL-3.0-or-later",
    // Weak copyleft: the prebuilt libvips shared library that sharp (Next.js
    // image optimization) loads dynamically, unmodified, from its own package.
    reason: "unmodified, dynamically loaded library; weak copyleft",
  },
  {
    ecosystem: "pypi",
    name: /^psycopg(-binary|-pool)?$/,
    license: "LGPL-3.0-only",
    // Weak copyleft: the PostgreSQL driver, imported unmodified as a library.
    reason: "unmodified library; weak copyleft",
  },
  {
    ecosystem: "npm",
    name: /^lightningcss(-[a-z0-9-]+)?$/,
    license: "MPL-2.0",
    // File-level copyleft: Vite's CSS transformer and its per-platform
    // binaries, used unmodified. They reach the production dependency tree
    // only through vite, an optional peer of @sentry/cloudflare, which
    // @sentry/nextjs depends on from 11.10.0; the web app's bundle and traced
    // server output do not load them. Accepted by the owner on 2026-11-07.
    reason: "unmodified build-time CSS tool; file-level copyleft; accepted by the owner 2026-11-07",
  },
  {
    ecosystem: "pypi",
    name: /^certifi$/,
    license: "MPL-2.0",
    // File-level copyleft: Mozilla's CA bundle, used unmodified.
    reason: "unmodified; file-level copyleft",
  },
  {
    ecosystem: "pypi",
    name: /^tzdata$/,
    license: "",
    // Required only on Windows (sys_platform == 'win32'), so it is not
    // installed where this check runs and its metadata cannot be read. The
    // upstream package declares Apache-2.0.
    reason: "Windows-only; not installed here",
  },
];

/** PyPI trove classifiers and other common non-SPDX spellings, mapped to SPDX. */
const ALIASES = new Map([
  ["MIT License", "MIT"],
  ["BSD License", "BSD-3-Clause"],
  ["Apache Software License", "Apache-2.0"],
  ["Apache License 2.0", "Apache-2.0"],
  ["ISC License (ISCL)", "ISC"],
  ["Python Software Foundation License", "PSF-2.0"],
  ["The Unlicense (Unlicense)", "Unlicense"],
  ["Mozilla Public License 2.0 (MPL 2.0)", "MPL-2.0"],
  ["GNU Lesser General Public License v3 (LGPLv3)", "LGPL-3.0-only"],
  ["GNU Library or Lesser General Public License (LGPL)", "LGPL-3.0-only"],
]);

const normalize = (license) => ALIASES.get(license.trim()) ?? license.trim();

/**
 * Evaluates an SPDX expression against the allowlist. Operators are
 * case-sensitive, as SPDX specifies, so the "-or-" in GPL-2.0-or-later is
 * part of the identifier. OR passes when any branch passes; AND when all do.
 * "X WITH exception" passes when X does and the exception is in ALLOWED_WITH;
 * any other exception fails the whole expression. Anything unparseable fails.
 */
function allowedExpression(expression, allowed = ALLOWED, allowedWith = ALLOWED_WITH) {
  const aliased = ALIASES.get(expression.trim());
  if (aliased) return allowed.has(aliased);
  // Classifier lists are joined with " OR " by python_licenses.py; map each part.
  const tokens = expression
    .split(" OR ")
    .map(normalize)
    .join(" OR ")
    .replace(/[()]/g, " $& ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  let position = 0;
  const peek = () => tokens[position];
  function primary() {
    const token = tokens[position++];
    if (token === "(") {
      const value = or();
      if (tokens[position++] !== ")") throw new Error("unbalanced parentheses");
      return value;
    }
    if (!token || ["AND", "OR", "WITH", ")"].includes(token)) throw new Error("expected a license");
    if (peek() === "WITH") {
      position += 1;
      const exception = tokens[position++];
      if (!exception || ["AND", "OR", "WITH", "(", ")"].includes(exception)) throw new Error("expected an exception");
      if (!allowedWith.has(exception)) throw new Error(`exception not accepted: ${exception}`);
    }
    return allowed.has(normalize(token));
  }
  function and() {
    let value = primary();
    while (peek() === "AND") {
      position += 1;
      value = primary() && value;
    }
    return value;
  }
  function or() {
    let value = and();
    while (peek() === "OR") {
      position += 1;
      value = and() || value;
    }
    return value;
  }
  try {
    if (!tokens.length) return false;
    const value = or();
    return position === tokens.length && value;
  } catch {
    return false;
  }
}

function selfTest() {
  const cases = [
    ["MIT", true],
    ["(MIT OR CC0-1.0)", true],
    ["(MPL-2.0 OR Apache-2.0)", true],
    ["(Apache-2.0 AND MIT)", true],
    ["Apache-2.0 OR BSD-3-Clause", true],
    ["BSD License OR Apache Software License", true],
    ["MIT License", true],
    ["Apache-2.0 WITH LLVM-exception", true],
    ["(Apache-2.0 WITH LLVM-exception) OR MIT", true],
    ["MIT AND Apache-2.0 WITH LLVM-exception", true],
    ["Apache-2.0 WITH Commons-Clause", false],
    ["Apache-2.0 WITH llvm-exception", false],
    ["Apache-2.0 WITH Commons-Clause OR MIT", false],
    ["MIT WITH AND", false],
    ["MIT WITH OR MIT", false],
    ["MIT WITH WITH", false],
    ["MIT WITH", false],
    ["MIT WITH (LLVM-exception)", false],
    ["MIT WITH MIT", false],
    ["Apache-2.0 WITH LLVM-exception WITH LLVM-exception", false],
    ["(MIT) WITH LLVM-exception", false],
    ["WITH LLVM-exception", false],
    ["LLVM-exception", false],
    ["Apache-2.0 WITH LLVM-exception AND GPL-3.0-only", false],
    ["GPL-2.0-only WITH Classpath-exception-2.0", false],
    ["GPL-3.0-or-later WITH GCC-exception-3.1", false],
    ["GPL-2.0-or-later", false],
    ["LGPL-3.0-or-later", false],
    ["MIT AND GPL-3.0-only", false],
    ["(MIT AND GPL-3.0-only) OR AGPL-3.0", false],
    ["GPL-3.0-only OR MIT", true],
    ["mit or gpl-3.0", false],
    ["MIT or GPL-3.0", false],
    ["FSL-1.1-Apache-2.0", false],
    ["BUSL-1.1", false],
    ["SSPL-1.0", false],
    ["UNLICENSED", false],
    ["Unknown", false],
    ["", false],
    ["(MIT", false],
    ["MIT OR", false],
    ["SEE LICENSE IN LICENSE.md", false],
  ];
  const failures = cases.filter(([expression, expected]) => allowedExpression(expression) !== expected);
  for (const [expression, expected] of failures) console.error(`  ${JSON.stringify(expression)}: expected ${expected}`);
  if (failures.length) {
    console.error("License evaluator self-test FAILED.");
    process.exit(1);
  }
  console.log(`License evaluator self-test passed (${cases.length} cases).`);
}

if (process.argv.includes("--self-test")) {
  selfTest();
  process.exit(0);
}

const failures = [];
const excepted = [];
const usedExceptions = new Set();

function check(ecosystem, name, license) {
  const expression = license ?? "";
  if (allowedExpression(expression)) return;
  const exception = EXCEPTIONS.find(
    (entry) => entry.ecosystem === ecosystem && entry.name.test(name) && normalize(entry.license) === normalize(expression),
  );
  if (exception) {
    usedExceptions.add(exception);
    excepted.push(`${ecosystem}: ${name} (${expression || "no metadata"}): ${exception.reason}`);
  } else {
    failures.push(`${ecosystem}: ${name} (${expression || "no license metadata"})`);
  }
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

if (excepted.length) console.log(`Reviewed exceptions:\n  ${excepted.join("\n  ")}`);
const unused = EXCEPTIONS.filter((entry) => !usedExceptions.has(entry));
if (unused.length) {
  console.warn(`Exceptions that matched nothing (remove them if the package is gone):\n  ${unused.map((e) => `${e.ecosystem}: ${e.name}`).join("\n  ")}`);
}
if (failures.length) {
  console.error(
    `Licenses not on the allowlist (SEC-10). Replace the dependency, or add a reviewed exception with a reason:\n  ${failures.join("\n  ")}`,
  );
  process.exit(1);
}
console.log(`License check passed (${Object.values(js).flat().length} npm, ${py.length} PyPI packages).`);
