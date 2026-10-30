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
 *     [--db-port 54330] [--keep-template-material] [--dry-run]
 *
 * The contact options are required while the placeholders are still present.
 * A copy is renamed once: a re-run with the same values changes nothing (a
 * later mention of the template in the copy is the adopter's and stays), and
 * a re-run with a different name, slug, port, or contact is refused. Every
 * target is checked for write access first, and package.json (the marker of an
 * initialized copy) is written last, so a run that still fails part-way can be
 * repeated with the same options to finish.
 *
 * The template's own records are not the adopter's: unless
 * `--keep-template-material` is given, the first run also removes the DEC-001
 * deployment proof (proofs/), the integration notes (docs-notes/), and the
 * template's evidence files (dev-docs/evidence/, which then holds only a README
 * for the adopter's own); replaces AGENTS.md with a neutral version that keeps the
 * engineering rules but not the template owner's open-decisions regime; adds a
 * banner to the specification, which stays as inherited reference with the ADRs;
 * drops prose wrapped in template-only markers from the Markdown files; and
 * turns links to the removed files into plain text. A copy that has finished
 * initializing is not touched again, so the adopter's later edits stay.
 */
import {
  accessSync,
  constants,
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { markdownFiles, unlinkRemoved } from "./lib/doc-links.mjs";
import {
  ADOPTER_TEMPLATES,
  EVIDENCE_DIR,
  ORIGINAL_DB_PORT,
  ORIGINAL_NAME,
  ORIGINAL_SLUG,
  PLACEHOLDERS,
  SPEC_BANNER,
  SPEC_BANNER_MARKER,
  SPEC_FILE,
  TAKEN_PORTS,
  TEMPLATE_ONLY_BLOCK,
  TEMPLATE_ONLY_PATHS,
  isValidContact,
  isValidName,
  isValidSlug,
} from "./lib/template.mjs";

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
    'Usage: pnpm init-template --name "Product Name" --slug product-slug --security-contact <email|https-url> --conduct-contact <email|https-url> [--db-port 54330] [--keep-template-material] [--dry-run]',
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
    "keep-template-material": { type: "boolean", default: false },
    "dry-run": { type: "boolean", default: false },
  },
});

const name = values.name?.trim();
const slug = values.slug?.trim();
const dbPort = values["db-port"]?.trim();
const dryRun = values["dry-run"];
const keepTemplateMaterial = values["keep-template-material"];

if (!isValidName(name)) {
  usage(
    "--name is required: up to 60 characters of ASCII letters, digits, single spaces, and . , ( ) + ! -, starting with a letter or digit, and not containing the template's name.",
  );
}
if (!isValidSlug(slug)) {
  usage("--slug is required: lowercase letters, digits, and hyphens, starting with a letter, and not the template's slug.");
}
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

// Renamed for good once package.json (written last) is renamed and neither the
// API title nor the generated OpenAPI title is the template's: an older run
// wrote package.json first and openapi.json last. The template's name in the
// copy's files is then the adopter's own text.
const renameDone =
  initialized &&
  currentName !== ORIGINAL_NAME &&
  !read("packages/contracts/openapi.json").includes(`"title": "${ORIGINAL_NAME} API"`);
if (!renameDone) {
  for (const rel of IDENTIFIER_FILES) {
    rewrite(rel, (text) => text.replaceAll(ORIGINAL_NAME, name).replaceAll(ORIGINAL_SLUG, slug));
  }
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

/** Files and folders the adopter's copy drops (repository-relative), computed before anything is removed. */
const removals = [];
function create(rel, text) {
  const file = path.join(root, rel);
  if (!existsSync(file) || readFileSync(file, "utf8") !== text) pending.set(rel, text);
  else pending.delete(rel);
}
// Applies until the copy has finished initializing (package.json, written last, is renamed): a run that failed
// part-way repeats it harmlessly, and a finished copy keeps whatever its owner has done since.
if (!renameDone && !keepTemplateMaterial) {
  for (const rel of TEMPLATE_ONLY_PATHS) if (existsSync(path.join(root, rel))) removals.push(rel);
  if (existsSync(path.join(root, EVIDENCE_DIR))) {
    for (const entry of readdirSync(path.join(root, EVIDENCE_DIR))) {
      if (entry !== "README.md") removals.push(`${EVIDENCE_DIR}/${entry}`);
    }
  }
  const adopterAgents = read(`${ADOPTER_TEMPLATES}/AGENTS.md`);
  if (adopterAgents) create("AGENTS.md", adopterAgents);
  const evidenceReadme = read(`${ADOPTER_TEMPLATES}/evidence-README.md`);
  if (evidenceReadme) create(`${EVIDENCE_DIR}/README.md`, evidenceReadme);
  rewrite(SPEC_FILE, (text) =>
    text.includes(SPEC_BANNER_MARKER) ? text : text.replace(/^(# .*\n)/, `$1\n${SPEC_BANNER}\n`),
  );
  const removedAbsolute = removals.map((rel) => path.join(root, rel));
  for (const rel of markdownFiles(root)) {
    if (removals.some((gone) => rel === gone || rel.startsWith(`${gone}/`))) continue;
    rewrite(rel, (text) => unlinkRemoved(text.replace(TEMPLATE_ONLY_BLOCK, ""), rel, root, removedAbsolute));
  }
}

const envFile = path.join(root, ".env");
const envCreated = !existsSync(envFile);

if (!dryRun) {
  // Check every target first, so a permission problem changes nothing.
  const targets = [...pending.keys()].map((rel) => [rel, constants.W_OK]);
  for (const rel of removals) targets.push([path.dirname(rel), constants.W_OK | constants.X_OK]);
  if (envCreated) targets.push([".", constants.W_OK | constants.X_OK]);
  for (const [rel, mode] of targets) {
    try {
      accessSync(path.join(root, existsSync(path.join(root, rel)) ? rel : path.dirname(rel)), mode);
    } catch {
      console.error(`${rel === "." ? "The project folder" : rel} is not writable; nothing was changed.`);
      process.exit(2);
    }
  }
  // bootstrap.py first and package.json last: they mark a run in progress and
  // a finished one (see the identity check above).
  const rank = (rel) => (rel === "apps/api/app/bootstrap.py" ? 0 : rel === "package.json" ? 2 : 1);
  const order = [...pending.keys()].sort((a, b) => rank(a) - rank(b));
  for (const rel of removals) rmSync(path.join(root, rel), { recursive: true, force: true });
  for (const rel of order) {
    mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    writeFileSync(path.join(root, rel), pending.get(rel));
  }
  if (envCreated) copyFileSync(path.join(root, ".env.example"), envFile);
}

const changes = new Set(pending.keys());
console.log(JSON.stringify({ dryRun, updated: [...changes].sort(), removed: removals.sort(), envCreated }, null, 2));
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
