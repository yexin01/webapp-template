#!/usr/bin/env node
/**
 * Template initialization (REPO-07). Renames the project's identifiers,
 * fills in the community contacts (DEC-012, ADR-0002), optionally moves the
 * local PostgreSQL port (so several projects created from this template can
 * run side by side), and creates a local .env from .env.example. Never copies
 * or prints credentials. Safe to re-run.
 *
 *   pnpm init-template --name "Acme Cloud" --slug acme-cloud \
 *     --security-contact security@acme.example --conduct-contact conduct@acme.example \
 *     [--db-port 54330] [--dry-run]
 *
 * The contact options are required while the placeholders are still present.
 * A copy is renamed once: a re-run with the same values changes nothing, and
 * a re-run with a different name, slug, port, or contact is refused.
 *
 * The specification and evidence under dev-docs/ describe the template itself
 * and are intentionally left unchanged.
 */
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { ORIGINAL_DB_PORT, ORIGINAL_NAME, ORIGINAL_SLUG, PLACEHOLDERS, isValidContact } from "./lib/template.mjs";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");

/** Files that contain the display name or slug. */
const IDENTIFIER_FILES = [
  "package.json",
  "README.md",
  "apps/api/pyproject.toml",
  "apps/api/app/__init__.py",
  "apps/api/app/bootstrap.py",
  "apps/web/src/app/layout.tsx",
  "apps/web/src/ui/site-header.tsx",
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
    'Usage: pnpm init-template --name "Product Name" --slug product-slug --security-contact <email|https-url> --conduct-contact <email|https-url> [--db-port 54330] [--dry-run]',
  );
  process.exit(2);
}

const { values } = parseArgs({
  options: {
    name: { type: "string" },
    slug: { type: "string" },
    "db-port": { type: "string" },
    "security-contact": { type: "string" },
    "conduct-contact": { type: "string" },
    "dry-run": { type: "boolean", default: false },
  },
});

const name = values.name?.trim();
const slug = values.slug?.trim();
const dbPort = values["db-port"]?.trim();
const dryRun = values["dry-run"];

// The name lands verbatim in JSON, Python, TOML, TSX, and Markdown, and the
// API's OpenAPI generator escapes non-ASCII characters, so only printable
// ASCII that is inert in all of them is accepted.
const SAFE_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9.,()+!-]| (?! ))*$/;
// A name or slug that contains the original would be renamed again on every re-run.
const ORIGINAL = /saas[ _-]?template/i;

if (!name || name.length > 60 || !SAFE_NAME.test(name) || ORIGINAL.test(name)) {
  usage(
    "--name is required: up to 60 characters of ASCII letters, digits, single spaces, and . , ( ) + ! -, starting with a letter or digit, and not containing the template's name.",
  );
}
if (!slug || !/^[a-z][a-z0-9-]{1,40}$/.test(slug) || ORIGINAL.test(slug)) {
  usage("--slug is required: lowercase letters, digits, and hyphens, starting with a letter, and not the template's slug.");
}
if (dbPort !== undefined && (!/^\d{4,5}$/.test(dbPort) || Number(dbPort) < 1024 || Number(dbPort) > 65535)) {
  usage("--db-port must be a number between 1024 and 65535.");
}
const read = (rel) => (existsSync(path.join(root, rel)) ? readFileSync(path.join(root, rel), "utf8") : "");
for (const { file, token, option } of PLACEHOLDERS) {
  const value = values[option]?.trim();
  if (value !== undefined && !isValidContact(value)) {
    usage(`--${option} must be an email address or an https URL.`);
  }
  const text = read(file);
  if (value === undefined && text.includes(token)) {
    usage(`--${option} is required: ${file} still contains ${token}.`);
  }
  if (value !== undefined && text && !text.includes(token) && !text.includes(value)) {
    usage(`--${option}: ${file} already has a different contact. Edit it by hand to change it.`);
  }
}

// A copy is renamed once. Once package.json no longer carries the template's
// slug, a re-run must repeat the same identity; anything else would silently
// do nothing, because the original identifiers are gone.
const currentSlug = JSON.parse(read("package.json") || "{}").name;
if (currentSlug && currentSlug !== ORIGINAL_SLUG) {
  const currentName = /^API_TITLE = "(.*) API"$/m.exec(read("apps/api/app/bootstrap.py"))?.[1];
  if (slug !== currentSlug || (currentName !== undefined && name !== currentName)) {
    usage(
      `This copy is already initialized as "${currentName ?? currentSlug}" (${currentSlug}). init-template renames a copy only once: re-run it with the same --name and --slug, or rename by hand.`,
    );
  }
  const currentPort = /127\.0\.0\.1:(\d+):5432/.exec(read("docker-compose.yml"))?.[1];
  if (dbPort && currentPort && currentPort !== ORIGINAL_DB_PORT && currentPort !== dbPort) {
    usage(`The local PostgreSQL port was already moved to ${currentPort}. Change it by hand to move it again.`);
  }
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
for (const { file, token, option } of PLACEHOLDERS) {
  const value = values[option]?.trim();
  if (value !== undefined) rewrite(file, (text) => text.replaceAll(token, () => value));
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
