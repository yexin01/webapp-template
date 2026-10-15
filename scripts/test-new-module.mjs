#!/usr/bin/env node
/**
 * Generator check (ADR-0003): copies the repository's tracked files
 * (scripts/lib/tracked-copy.mjs) to a temporary git checkout, runs
 * `new:module` there, and proves the generated modules pass the same gates as
 * hand-written code:
 * - API: lint, format, strict types, import contracts, architecture tests,
 *   and the generated integration tests (including the DB-07 row-level
 *   security tests) against local PostgreSQL;
 * - contracts: regeneration;
 * - web: types, lint, and the generated component tests.
 * Three modules are generated: a typical one, a one-letter name (the shortest
 * allowed), and the longest name and entity allowed.
 *
 * It also checks that bad input, names that collide with existing code (taken
 * from the copy itself), and names git would ignore are refused without
 * writing anything; that a dry run writes nothing; that entity names are
 * derived only from clear plurals; and that a failed write leaves nothing
 * behind. Fixture names carry a random prefix, so they cannot clash with
 * modules an adopter has generated.
 *
 * Never touches the working tree: the API runs in its own virtual environment
 * inside the copy (built from uv.lock and uv's cache), and the web tools reuse
 * node_modules through symlinks with their caches off. Needs `pnpm bootstrap`
 * first.
 */
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  appendFileSync,
  chmodSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { devNull } from "node:os";
import path from "node:path";

import { copyTrackedFiles, untrackedFiles } from "./lib/tracked-copy.mjs";

const repo = path.resolve(import.meta.dirname, "..");
const failures = [];
const check = (ok, message) => ok || failures.push(message);

// A random prefix keeps fixture names clear of any real module.
const hex = randomBytes(3).toString("hex");
const prefix = `zz${hex}`;
const Prefix = `Zz${hex}`;
const fresh = (suffix) => `${prefix}_${suffix}`;

function copyRepo() {
  const dir = copyTrackedFiles(repo, "new-module-");
  for (const rel of ["node_modules", "apps/web/node_modules", "packages/contracts/node_modules"]) {
    if (existsSync(path.join(repo, rel))) symlinkSync(path.join(repo, rel), path.join(dir, rel), "dir");
  }
  // A git checkout (nothing committed), so the generator's ignore check runs.
  spawnSync("git", ["init", "-q"], { cwd: dir, env: gitEnv(dir) });
  return dir;
}

// The copy's own virtual environment: `uv run` syncs whichever environment it is given.
const env = (dir) => ({ ...process.env, UV_PROJECT_ENVIRONMENT: path.join(dir, "apps/api/.venv"), TEMPLATE_ROOT: dir });
// The copy's git sees only the copy's ignore rules: no global or system config,
// and no default global ignore file ($XDG_CONFIG_HOME/git/ignore).
const gitEnv = (dir) => ({
  ...env(dir),
  GIT_CONFIG_GLOBAL: devNull,
  GIT_CONFIG_NOSYSTEM: "1",
  XDG_CONFIG_HOME: path.join(dir, ".git", "no-global-config"),
});

function run(dir, cwd, cmd, args, label) {
  const result = spawnSync(cmd, args, { cwd: path.join(dir, cwd), encoding: "utf8", env: env(dir) });
  if (result.status !== 0) {
    const output = `${result.stdout ?? ""}${result.stderr ?? ""}${result.error?.message ?? ""}`;
    failures.push(`${label} failed:\n${output.slice(-3000)}`);
  }
  return { ...result, stdout: result.stdout ?? "" };
}

const untracked = untrackedFiles(repo);
if (untracked.length) {
  const shown = untracked.slice(0, 5).join(", ") + (untracked.length > 5 ? ", ..." : "");
  console.warn(`Note: ${untracked.length} untracked file(s) are not part of the copy (git add to include): ${shown}`);
}

const dir = copyRepo();
try {
  const generator = path.join(dir, "scripts/new-module.mjs");
  const gen = (args) => spawnSync(process.execPath, [generator, ...args], { cwd: dir, encoding: "utf8", env: gitEnv(dir) });
  const inCopy = (rel) => readFileSync(path.join(dir, rel), "utf8");
  // Everything the generator writes or edits.
  const snapshot = () =>
    JSON.stringify([
      ...["apps/api/app/modules", "apps/api/tests/integration", "apps/web/src/features", "supabase/migrations"].map(
        (rel) => readdirSync(path.join(dir, rel), { recursive: true }).sort(),
      ),
      ...["apps/api/app/bootstrap.py", "apps/web/src/lib/api/client.ts"].map(inCopy),
    ]);

  // -- What the copy already has --------------------------------------------------
  const valid = (name) => /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/.test(name);
  const existing = readdirSync(path.join(dir, "apps/api/app/modules"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && valid(entry.name))
    .map((entry) => entry.name);
  const isModule = (name) => existing.includes(name);
  const bootstrap = inCopy("apps/api/app/bootstrap.py");
  const inBootstrap = (id) => new RegExp(`\\b${id}\\b`).test(bootstrap);
  const contract = JSON.parse(inCopy("packages/contracts/openapi.json"));
  const routeUsed = (segment) => Object.keys(contract.paths).some((p) => p.split("/")[3] === segment);
  const schemas = Object.keys(contract.components?.schemas ?? {}).map((s) =>
    s.replace(/-(Input|Output)$/, "").split("__").at(-1),
  );
  const appSources = readdirSync(path.join(dir, "apps/api/app"), { recursive: true })
    .filter((f) => f.endsWith(".py"))
    .map((f) => inCopy(`apps/api/app/${f}`))
    .join("\n");
  const defined = (cls) => schemas.includes(cls) || new RegExp(`^\\s*class ${cls}\\b`, "m").test(appSources);
  const kernelNames = readdirSync(path.join(dir, "apps/api/app/kernel"), { recursive: true })
    .map((f) => path.basename(f, ".py"))
    .filter((n) => valid(n) && !isModule(n))
    .sort();
  const kernelName = kernelNames.includes("probes") ? "probes" : kernelNames[0];

  // -- Refusals: bad input and collisions, refused with a reason before any write ---
  const refusals = [
    [[], /must be snake_case/],
    [["Bad-Name"], /must be snake_case/],
    [[fresh("items"), "--entity", "lowercase"], /must be PascalCase/],
    [[fresh("items"), "--entity", "URL"], /must be PascalCase/],
    [["class"], /"class" is a Python keyword/],
    [["order", "--entity", "OrderItem"], /"order" is a PostgreSQL reserved word/],
    [[fresh("items"), "--entity", "None"], /"None" is a Python keyword/],
    [[fresh("passes"), "--entity", "Pass"], /"pass" is a Python keyword/],
    // No clear singular: the entity must be named.
    ...["data", "series", "news", "status", "caches", "quizzes", "abuses", "s"].map((word) => [
      [fresh(word)],
      /Cannot derive a singular entity name/,
    ]),
    // Every module the copy has.
    ...existing.map((name) => [[name], /is an existing module|is a Python keyword|is a PostgreSQL reserved word/]),
    // FastAPI would rename the existing public schema and break the typed web client.
    ...schemas
      .filter((s) => /^[A-Z][a-z]+$/.test(s))
      .slice(0, 5)
      .map((s) => [
        [fresh("items"), "--entity", s],
        new RegExp(`"${s}" is already defined in packages/contracts/openapi\\.json\\. Pass a distinct --entity`),
      ]),
  ];
  if (kernelName) refusals.push([[kernelName], /kernel name/]);
  if (!isModule("s")) refusals.push([["s"], /Cannot derive a singular entity name/]);
  // Reserved names, including the alias the generated row-level security policy uses.
  for (const name of ["kernel", "membership"]) {
    if (!isModule(name) && !kernelNames.includes(name)) refusals.push([[name, "--entity", `${Prefix}Thing`], /reserved/]);
  }
  // Locals, fields, methods, and imports of the composition root, where the copy has them.
  for (const [args, id] of [
    [["analytics"], "analytics"],
    [["closers"], "closers"],
    [["close", "--entity", `${Prefix}Thing`], "close"],
    [["container", "--entity", `${Prefix}Box`], "container"],
  ]) {
    if (inBootstrap(id) && !isModule(args[0])) {
      refusals.push([args, new RegExp(`collide with "${id}" in apps/api/app/bootstrap\\.py`)]);
    }
  }
  if (routeUsed("health") && !isModule("health") && !inBootstrap("health")) {
    refusals.push([["health", "--entity", `${Prefix}Check`], /route \/api\/v1\/health is already used/]);
  }
  // Names the generated module itself uses, and the fields of its event.
  if (!defined("Response")) {
    refusals.push([[fresh("items"), "--entity", "Response"], /"Response" would shadow a name the generated module uses/]);
  }
  if (!defined("Org")) refusals.push([[fresh("accounts"), "--entity", "Org"], /"org_id" would duplicate a field/]);
  // A file git would ignore.
  mkdirSync(path.join(dir, ".git/info"), { recursive: true });
  appendFileSync(path.join(dir, ".git/info/exclude"), `/apps/web/src/features/${prefix}-hidden-items/\n`);
  refusals.push([[fresh("hidden_items")], /would be ignored by git/]);

  const untouched = snapshot();
  for (const [args, reason] of refusals) {
    const refused = gen(args);
    check(
      refused.status === 2 && reason.test(refused.stderr),
      `not refused with ${reason}: ${args.join(" ") || "(none)"} (exit ${refused.status})\n${refused.stderr}`,
    );
  }
  check(snapshot() === untouched, "refused input wrote files");

  // Names that broad ignore rules used to hide are accepted (where the copy has no such module or route).
  for (const name of ["logs", "tmp", "secrets", "build", "coverage", "test_results"]) {
    if (isModule(name) || inBootstrap(name) || routeUsed(name.replaceAll("_", "-"))) continue;
    const planned = gen([name, "--entity", `${Prefix}Thing`, "--dry-run"]);
    check(planned.status === 0, `"${name}" was refused (exit ${planned.status}):\n${planned.stderr}`);
  }

  // -- Entity names come only from clear plurals -------------------------------------
  for (const [word, entity] of Object.entries({
    notes: "Note",
    time_entries: "TimeEntry",
    categories: "Category",
    movies: "Movie",
    statuses: "Status",
    boxes: "Box",
    addresses: "Address",
    matches: "Match",
    cases: "Case",
    houses: "House",
    emojis: "Emoji",
    people: "Person",
  })) {
    const planned = gen([fresh(word), "--dry-run"]);
    const derived = planned.status === 0 ? JSON.parse(planned.stdout).entity : planned.stderr.trim();
    check(derived === `${Prefix}${entity}`, `${fresh(word)} should derive ${Prefix}${entity}, got: ${derived}`);
  }
  check(snapshot() === untouched, "dry runs wrote files");

  // -- Unwritable targets are refused up front (root ignores permissions) -------------
  // The directory of a new file, and the directory of an edited file (written by rename).
  if (process.getuid?.() !== 0) {
    for (const rel of ["apps/web/src/features", "apps/web/src/lib/api"]) {
      chmodSync(path.join(dir, rel), 0o555);
      const blocked = gen([fresh("blocked_items")]);
      chmodSync(path.join(dir, rel), 0o755);
      check(
        blocked.status === 2 && /not a writable directory/.test(blocked.stderr),
        `an unwritable ${rel} was not refused up front (exit ${blocked.status}):\n${blocked.stderr}`,
      );
    }
    check(snapshot() === untouched, "a refused unwritable target left files behind");
  }

  // -- A write that fails part-way is rolled back ----------------------------------------
  // A temporary file left by an interrupted run blocks the last write (client.ts)
  // after every other file was written; the run must undo them and keep that file.
  const stale = path.join(dir, "apps/web/src/lib/api/.client.ts.new-module.tmp");
  writeFileSync(stale, "left by an interrupted run\n");
  const interrupted = gen([fresh("rollback_items")]);
  check(
    interrupted.status === 1 && /Every change was rolled back/.test(interrupted.stderr),
    `a write that failed part-way was not rolled back (exit ${interrupted.status}):\n${interrupted.stderr}`,
  );
  check(snapshot() === untouched, "a write that failed part-way left files behind");
  check(existsSync(stale), "the rollback removed a file it did not create");
  rmSync(stale, { force: true });

  // -- Generate for real ---------------------------------------------------------------
  const MAIN = fresh("time_entries");
  // The shortest name: once, a one-letter module's table shadowed the policy's alias.
  const SHORT = [..."mabcdefghijklnopqrstuvwxyz"].find(
    (letter) => !isModule(letter) && gen([letter, "--entity", `${Prefix}Letter`, "--dry-run"]).status === 0,
  );
  check(SHORT, "no one-letter module name is available");
  // The longest name and entity: 30 characters, and 41 with the most capitals (the longest snake_case form).
  const LONG = `${prefix}_${"x".repeat(30 - prefix.length - "__items".length)}_items`;
  const LONG_ENTITY = `Za${[...hex].map((d) => "ABCDEFGHIJKLMNOP"[parseInt(d, 16)]).join("")}${"Q".repeat(33)}`;
  const modules = [
    [MAIN, []],
    [SHORT, ["--entity", `${Prefix}Letter`]],
    [LONG, ["--entity", LONG_ENTITY]],
  ];
  check(gen([MAIN, "--dry-run"]).status === 0, "dry run failed");
  check(snapshot() === untouched, "dry run wrote files");
  for (const [name, args] of modules) {
    const result = gen([name, ...args]);
    check(result.status === 0, `generator failed for ${name}: ${result.stderr}`);
  }
  check(/is an existing module/.test(gen([MAIN]).stderr), "generating the same module twice was accepted");

  const uv = (args, label) => run(dir, "apps/api", "uv", ["run", "--frozen", ...args], label);
  uv(["ruff", "check", "."], "ruff check");
  uv(["ruff", "format", "--check", "."], "ruff format");
  uv(["mypy"], "mypy");
  uv(["lint-imports"], "import contracts");
  const apiTests = modules.map(([name]) => `tests/integration/test_${name}.py`);
  const pytest = uv(["pytest", "-q", "-p", "no:cacheprovider", "-rs", "tests/architecture", ...apiTests], "API tests");
  // Database tests skip when PostgreSQL is down; a skip must not count as a pass.
  const perModule = (inCopy("scripts/templates/module/test_module.py.tmpl").match(/^def test_/gm) ?? []).length;
  const passed = Number(/(\d+) passed/.exec(pytest.stdout)?.[1] ?? 0);
  check(
    passed >= modules.length * perModule && !/\b\d+ (skipped|failed|errors?)\b/.test(pytest.stdout),
    `API tests did not all run:\n${pytest.stdout.slice(-800)}`,
  );

  uv(["python", "-m", "app.cli", "openapi", "--output", "../../packages/contracts/openapi.json"], "OpenAPI export");
  run(dir, "apps/web", "node_modules/.bin/openapi-typescript", ["../../packages/contracts/openapi.json", "-o", "src/lib/api/schema.d.ts"], "web contract types");
  run(dir, "apps/web", "node_modules/.bin/tsc", ["--noEmit"], "web typecheck");
  const features = modules.map(([name]) => `src/features/${name.replaceAll("_", "-")}`);
  run(dir, "apps/web", "node_modules/.bin/eslint", [...features, "src/lib/api/client.ts", "--max-warnings=0"], "web lint");
  // Caches off: they would be written into the working tree's node_modules through the symlink.
  const vitest = run(dir, "apps/web", "node_modules/.bin/vitest", ["run", "--no-cache", "--no-fsModuleCache", ...features], "web component test");
  const webPerModule = (inCopy("scripts/templates/module/web/panel.test.tsx.tmpl").match(/^\s*(it|test)\(/gm) ?? []).length;
  const webPassed = Number(/Tests\s+(\d+) passed/.exec(vitest.stdout)?.[1] ?? 0);
  check(
    webPassed >= modules.length * Math.max(1, webPerModule) && !/\b\d+ failed\b/.test(vitest.stdout),
    `web component tests did not all run:\n${vitest.stdout.slice(-800)}`,
  );
} finally {
  rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`new:module check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(
  "new:module check passed: bad, colliding, and ignored names refused; entities derived only from clear plurals; dry run and failed writes leave nothing behind; typical, one-letter, and longest modules pass lint, types, contracts, architecture, API (including RLS) and web tests.",
);
