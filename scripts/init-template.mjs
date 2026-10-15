#!/usr/bin/env node
/**
 * Template initialization (REPO-07). Renames the project's identifiers,
 * fills in the community contacts (DEC-012, ADR-0002), optionally moves the
 * local PostgreSQL port (so several projects created from this template can
 * run side by side), names the Docker Compose project after the slug (so
 * copies in folders with the same name do not share a database volume), and
 * creates a local .env from .env.example. Never copies or prints credentials.
 * Safe to re-run.
 *
 *   pnpm init-template --name "Acme Cloud" --slug acme-cloud \
 *     --security-contact security@acme.example --conduct-contact conduct@acme.example \
 *     [--db-port 54330] [--dry-run]
 *
 * The contact options are required while the placeholders are still present.
 * A copy is renamed once: a re-run with the same values changes nothing, and
 * a re-run with a different name, slug, port, or contact is refused. Every
 * target is checked for write access first, and package.json (the marker of an
 * initialized copy) is written last, so a run that still fails part-way can be
 * repeated with the same options to finish.
 *
 * The specification and evidence under dev-docs/ describe the template itself
 * and are intentionally left unchanged.
 */
import { accessSync, constants, copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
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
// Ports the template's own servers use: web, API, and the performance server.
const TAKEN_PORTS = new Set(["3000", "8000", "8001"]);
if (
  dbPort !== undefined &&
  (!/^[1-9]\d{3,4}$/.test(dbPort) || Number(dbPort) < 1024 || Number(dbPort) > 65535 || TAKEN_PORTS.has(dbPort))
) {
  usage(
    `--db-port must be a whole number from 1024 to 65535, without leading zeros, and not ${[...TAKEN_PORTS].join(", ")} (used by the template's servers).`,
  );
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

// A copy is renamed once. Once package.json or the API title no longer carries
// the template's identity, a re-run must repeat the same identity; anything
// else would silently do nothing, because the original identifiers are gone.
// A copy with only some files renamed (a run that failed part-way) is resumed
// by the same options: bootstrap.py, which carries the name, is written first,
// and package.json, which carries the slug, last. Before package.json, the
// Compose project name (written by the same run) carries the slug.
const currentSlug = JSON.parse(read("package.json") || "{}").name;
const currentName = /^API_TITLE = "(.*) API"$/m.exec(read("apps/api/app/bootstrap.py"))?.[1];
const composeName = /^name:\s*["']?([^\s"'#]+)/m.exec(read("docker-compose.yml"))?.[1];
const initialized = Boolean(currentSlug && currentSlug !== ORIGINAL_SLUG);
const renamedName = currentName !== undefined && currentName !== ORIGINAL_NAME ? currentName : undefined;
const renamedSlug = initialized ? currentSlug : renamedName ? composeName : undefined;
if ((renamedSlug && slug !== renamedSlug) || (renamedName && name !== renamedName)) {
  usage(
    `This copy is already initialized as "${renamedName ?? currentName ?? renamedSlug}" (${renamedSlug ?? currentSlug}). init-template renames a copy only once: re-run it with the same --name and --slug, or rename by hand.`,
  );
}
const currentPort = /127\.0\.0\.1:(\d+):5432/.exec(read("docker-compose.yml"))?.[1];
if (dbPort && currentPort && currentPort !== ORIGINAL_DB_PORT && currentPort !== dbPort) {
  usage(`The local PostgreSQL port was already moved to ${currentPort}. Change it by hand to move it again.`);
}

/** New contents by file, computed in full before anything is written. */
const pending = new Map();
function rewrite(rel, transform) {
  const file = path.join(root, rel);
  if (!existsSync(file)) return;
  const original = readFileSync(file, "utf8");
  const after = transform(pending.get(rel) ?? original);
  if (after !== original) pending.set(rel, after);
  else pending.delete(rel);
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
// Without a project name, Compose names the project (and its volume) after the
// folder, so two copies in folders with the same name would share a database.
// A copy initialized before this step existed keeps its folder-named project:
// renaming it would silently switch `pnpm db:up` to a new, empty volume.
let composeNotice;
if (initialized && composeName === undefined) {
  composeNotice = `docker-compose.yml was left without a project name, so your existing database volume stays in use. To stop copies in folders with the same name from sharing it, add "name: ${slug}" at the top yourself; \`pnpm db:up\` then starts a new, empty volume (dump and restore the data first if you need it).`;
} else {
  rewrite("docker-compose.yml", (text) =>
    /^name:/m.test(text) ? text : text.replace(/^services:/m, `name: ${slug}\n\nservices:`),
  );
}

const envFile = path.join(root, ".env");
const envCreated = !existsSync(envFile);

if (!dryRun) {
  // Check every target first, so a permission problem changes nothing.
  const targets = [...pending.keys()].map((rel) => [rel, constants.W_OK]);
  if (envCreated) targets.push([".", constants.W_OK | constants.X_OK]);
  for (const [rel, mode] of targets) {
    try {
      accessSync(path.join(root, rel), mode);
    } catch {
      console.error(`${rel === "." ? "The project folder" : rel} is not writable; nothing was changed.`);
      process.exit(2);
    }
  }
  // bootstrap.py first and package.json last: they mark a run in progress and
  // a finished one (see the identity check above).
  const rank = (rel) => (rel === "apps/api/app/bootstrap.py" ? 0 : rel === "package.json" ? 2 : 1);
  const order = [...pending.keys()].sort((a, b) => rank(a) - rank(b));
  for (const rel of order) writeFileSync(path.join(root, rel), pending.get(rel));
  if (envCreated) copyFileSync(path.join(root, ".env.example"), envFile);
}

const changes = new Set(pending.keys());
console.log(JSON.stringify({ dryRun, updated: [...changes].sort(), envCreated }, null, 2));
if (envCreated) {
  console.log("Next: fill in .env with your own sandbox keys (see docs/integrations.md).");
} else if (dbPort && changes.has(".env.example")) {
  console.log(`Your existing .env was not changed: update the port in DATABASE_URL to ${dbPort} yourself.`);
}
if (composeNotice) console.log(composeNotice);
else if (composeName === undefined && /^name:/m.test(pending.get("docker-compose.yml") ?? "")) {
  console.log(
    `The Docker Compose project is now named "${slug}": a database started before init (named after the folder) is no longer used.`,
  );
}
