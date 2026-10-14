#!/usr/bin/env node
/**
 * Scaffolds a tenant-scoped feature module (ADR-0003):
 * - the API module (domain, ports, service, adapters, routes, module);
 * - a migration with forced row-level security;
 * - integration tests;
 * - a web feature with its test;
 * - typed client methods;
 * - the wiring in app/bootstrap.py.
 *
 *   pnpm new:module <name> [--entity EntityName] [--dry-run]
 *
 * <name> is snake_case and plural: notes, time_entries. The generated code
 * follows the projects module. `pnpm test:new-module` checks this generator
 * end to end.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");
const templates = path.join(import.meta.dirname, "templates", "module");

const RESERVED = new Set([
  "api", "app", "audit", "billing", "diagnostics", "events", "identity", "jobs", "kernel", "llm", "modules",
  "organizations", "outbox", "projects", "settings", "subscriptions", "users", "webhooks",
]);

function fail(message) {
  console.error(message);
  console.error("Usage: pnpm new:module <name> [--entity EntityName] [--dry-run]   e.g. pnpm new:module notes");
  process.exit(2);
}

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { entity: { type: "string" }, "dry-run": { type: "boolean", default: false } },
});
const name = positionals[0];
const dryRun = values["dry-run"];

if (!name || !/^[a-z][a-z0-9]*(_[a-z0-9]+)*$/.test(name) || name.length > 30) {
  fail("<name> must be snake_case, start with a letter, and be at most 30 characters.");
}
if (RESERVED.has(name)) fail(`"${name}" is reserved.`);

const pascal = (snake) => snake.split("_").map((p) => p[0].toUpperCase() + p.slice(1)).join("");
const snake = (pascalName) => pascalName.replace(/(?<!^)([A-Z])/g, "_$1").toLowerCase();

let Entity = values.entity;
if (Entity === undefined) {
  if (name.endsWith("ies")) Entity = pascal(`${name.slice(0, -3)}y`);
  else if (name.endsWith("s") && !name.endsWith("ss")) Entity = pascal(name.slice(0, -1));
  else fail(`Cannot derive a singular entity name from "${name}"; pass --entity, e.g. --entity ${pascal(name)}Item.`);
}
if (!/^[A-Z][A-Za-z0-9]{1,40}$/.test(Entity)) fail("--entity must be PascalCase, e.g. TimeEntry.");
if (Entity === pascal(name)) fail("The entity name must differ from the module name; pass --entity.");

const tokens = {
  __ENTITY__: snake(Entity).toUpperCase(),
  __Entity__: Entity,
  __entity__: snake(Entity),
  __Module__: pascal(name),
  __module__: name,
  __path__: name.replaceAll("_", "-"),
  __label__: name.replaceAll("_", " "),
};
const render = (text) => Object.entries(tokens).reduce((out, [token, value]) => out.replaceAll(token, value), text);
const template = (rel) => render(readFileSync(path.join(templates, rel), "utf8"));

// -- Plan -------------------------------------------------------------------------

const migrationsDir = path.join(root, "supabase", "migrations");
const migrations = readdirSync(migrationsDir).filter((f) => /^\d{14}_.+\.sql$/.test(f)).sort();
if (migrations.some((f) => readFileSync(path.join(migrationsDir, f), "utf8").includes(`CREATE TABLE app.${name} `))) {
  fail(`A table app.${name} already exists.`);
}
const now = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
const last = migrations.at(-1)?.slice(0, 14) ?? "0";
const stamp = now > last ? now : String(BigInt(last) + 1n);

const api = `apps/api/app/modules/${name}`;
const feature = `apps/web/src/features/${tokens.__path__}`;
const files = {
  [`${api}/__init__.py`]: template("api/__init__.py.tmpl"),
  [`${api}/domain.py`]: template("api/domain.py.tmpl"),
  [`${api}/ports.py`]: template("api/ports.py.tmpl"),
  [`${api}/service.py`]: template("api/service.py.tmpl"),
  [`${api}/adapters.py`]: template("api/adapters.py.tmpl"),
  [`${api}/routes.py`]: template("api/routes.py.tmpl"),
  [`${api}/module.py`]: template("api/module.py.tmpl"),
  [`supabase/migrations/${stamp}_${name}.sql`]: template("migration.sql.tmpl"),
  [`apps/api/tests/integration/test_${name}.py`]: template("test_module.py.tmpl"),
  [`${feature}/index.ts`]: template("web/index.ts.tmpl"),
  [`${feature}/${tokens.__path__}-panel.tsx`]: template("web/panel.tsx.tmpl"),
  [`${feature}/${tokens.__path__}-panel.test.tsx`]: template("web/panel.test.tsx.tmpl"),
};
for (const rel of Object.keys(files)) {
  if (existsSync(path.join(root, rel))) fail(`${rel} already exists.`);
}

const M = tokens.__Module__;
const edits = [
  ["apps/api/app/bootstrap.py", "# new:module:imports", `from app.modules.${name}.module import ${M}Module, build_${name}\n`],
  ["apps/api/app/bootstrap.py", "    # new:module:fields", `    ${name}: ${M}Module\n`],
  ["apps/api/app/bootstrap.py", "            # new:module:contributions", `            self.${name}.contribution,\n`],
  ["apps/api/app/bootstrap.py", "    # new:module:build", `    ${name} = build_${name}(kernel, identity.auth)\n`],
  ["apps/api/app/bootstrap.py", "        # new:module:container", `        ${name}=${name},\n`],
  ["apps/web/src/lib/api/client.ts", "    // new:module:client", template("web/client-methods.ts.tmpl")],
];
const edited = new Map();
for (const [rel, marker, insertion] of edits) {
  const text = edited.get(rel) ?? readFileSync(path.join(root, rel), "utf8");
  const at = text.indexOf(marker);
  if (at === -1) fail(`Marker "${marker.trim()}" not found in ${rel}; was it removed?`);
  edited.set(rel, text.slice(0, at) + insertion + text.slice(at));
}

console.log(JSON.stringify({ dryRun, module: name, entity: Entity, create: Object.keys(files), update: [...edited.keys()] }, null, 2));
if (dryRun) process.exit(0);

// -- Write ------------------------------------------------------------------------

for (const [rel, text] of Object.entries(files)) {
  mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  writeFileSync(path.join(root, rel), text);
}
for (const [rel, text] of edited) writeFileSync(path.join(root, rel), text);

// Best effort: format generated code and re-sort the wiring imports.
const pyFiles = [...Object.keys(files).filter((f) => f.endsWith(".py")), "apps/api/app/bootstrap.py"].map((f) =>
  path.relative("apps/api", f),
);
const run = (cmd, args, cwd) => spawnSync(cmd, args, { cwd: path.join(root, cwd), stdio: "ignore" }).status === 0;
const pyOk =
  run("uv", ["run", "--frozen", "ruff", "check", "--fix", "--select", "I", ...pyFiles], "apps/api") &&
  run("uv", ["run", "--frozen", "ruff", "format", ...pyFiles], "apps/api");
const tsFiles = [...Object.keys(files).filter((f) => /\.tsx?$/.test(f)), "apps/web/src/lib/api/client.ts"].map((f) =>
  path.relative("apps/web", f),
);
const tsOk = run("pnpm", ["exec", "prettier", "--write", ...tsFiles], "apps/web");
if (!pyOk || !tsOk) console.warn("Formatting was skipped; run `pnpm lint` and fix any style findings.");

console.log(`
Next steps:
  1. pnpm db:migrate             # create app.${name} locally
  2. pnpm contracts              # regenerate the OpenAPI document and the typed web client
  3. pnpm test                   # the generated tests: tests/integration/test_${name}.py and the panel test
  4. Mount <${M}Panel /> from "@/features/${tokens.__path__}" in a page (for example the dashboard).
  5. Replace the placeholder description in ${api}/__init__.py and model your real fields.`);
