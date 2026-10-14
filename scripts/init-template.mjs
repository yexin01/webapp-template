#!/usr/bin/env node
/**
 * Template initialization (REPO-07). Renames the project's identifiers,
 * optionally moves the local PostgreSQL port (so several projects created
 * from this template can run side by side), and creates a local .env from
 * .env.example. Never copies or prints credentials. Safe to re-run.
 *
 *   pnpm init-template --name "Acme Cloud" --slug acme-cloud [--db-port 54330] [--dry-run]
 *
 * The specification and evidence under dev-docs/ describe the template itself
 * and are intentionally left unchanged.
 */
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");

const ORIGINAL_NAME = "SaaS Template";
const ORIGINAL_SLUG = "saas-template";
const ORIGINAL_DB_PORT = "54329";

/** Files that contain the display name or slug. */
const IDENTIFIER_FILES = [
  "package.json",
  "README.md",
  "apps/api/pyproject.toml",
  "apps/api/app/__init__.py",
  "apps/api/app/http/app.py",
  "apps/web/src/app/layout.tsx",
  "apps/web/src/components/site-header.tsx",
  "packages/contracts/openapi.json",
];

/** Files that contain the local PostgreSQL port. */
const PORT_FILES = [
  ".env.example",
  "docker-compose.yml",
  ".github/workflows/ci.yml",
  "README.md",
  "docs/integrations.md",
  "apps/api/tests/conftest.py",
  "apps/web/playwright.config.ts",
  "perf/server.py",
];

function usage(message) {
  console.error(message);
  console.error(
    'Usage: pnpm init-template --name "Product Name" --slug product-slug [--db-port 54330] [--dry-run]',
  );
  process.exit(2);
}

const { values } = parseArgs({
  options: {
    name: { type: "string" },
    slug: { type: "string" },
    "db-port": { type: "string" },
    "dry-run": { type: "boolean", default: false },
  },
});

const name = values.name?.trim();
const slug = values.slug?.trim();
const dbPort = values["db-port"]?.trim();
const dryRun = values["dry-run"];

if (!name || name.length > 60 || /["'<>`$\\{}]/.test(name)) {
  usage("--name is required: up to 60 characters, without quotes, braces, or backslashes.");
}
if (!slug || !/^[a-z][a-z0-9-]{1,40}$/.test(slug)) {
  usage("--slug is required: lowercase letters, digits, and hyphens, starting with a letter.");
}
if (dbPort !== undefined && (!/^\d{4,5}$/.test(dbPort) || Number(dbPort) < 1024 || Number(dbPort) > 65535)) {
  usage("--db-port must be a number between 1024 and 65535.");
}

const changes = new Set();
function rewrite(rel, transform) {
  const file = path.join(root, rel);
  if (!existsSync(file)) return;
  const before = readFileSync(file, "utf8");
  const after = transform(before);
  if (after !== before) {
    changes.add(rel);
    if (!dryRun) writeFileSync(file, after);
  }
}

for (const rel of IDENTIFIER_FILES) {
  rewrite(rel, (text) => text.replaceAll(ORIGINAL_NAME, name).replaceAll(ORIGINAL_SLUG, slug));
}
if (dbPort && dbPort !== ORIGINAL_DB_PORT) {
  // Only the exact port token next to a host or a port mapping is replaced.
  const pattern = new RegExp(`(127\\.0\\.0\\.1:|")${ORIGINAL_DB_PORT}(?=[:/)"\`\\s]|$)`, "gm");
  for (const rel of PORT_FILES) rewrite(rel, (text) => text.replace(pattern, `$1${dbPort}`));
}

const envFile = path.join(root, ".env");
const envCreated = !existsSync(envFile);
if (envCreated && !dryRun) copyFileSync(path.join(root, ".env.example"), envFile);

console.log(JSON.stringify({ dryRun, updated: [...changes].sort(), envCreated }, null, 2));
if (envCreated) {
  console.log("Next: fill in .env with your own sandbox keys (see docs/integrations.md).");
} else if (dbPort && changes.has(".env.example")) {
  console.log(`Your existing .env was not changed: update the port in DATABASE_URL to ${dbPort} yourself.`);
}
