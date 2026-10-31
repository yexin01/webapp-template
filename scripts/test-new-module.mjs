#!/usr/bin/env node
/**
 * Generator check (ADR-0003): copies the repository's tracked files
 * (scripts/lib/tracked-copy.mjs) to a temporary git checkout, runs
 * `new:module` there, and proves the generated modules pass the same gates as
 * hand-written code, so that `pnpm test` and `pnpm lint` stay green after
 * `pnpm new:module`:
 * - API: lint, format, strict types, import contracts, migration lint (squawk),
 *   and the whole test suite against local PostgreSQL, as `pnpm test:api` runs
 *   it (not a list of files: a test that enumerates routes or operations, such
 *   as the contract sweep and the write-limit manifest, breaks on a module
 *   that is missing from it), including each module's generated tests and the
 *   DB-07 row-level security tests;
 * - contracts: regeneration, before the tests, which compare every response
 *   with the document;
 * - web: types, lint, formatting, and the whole test suite;
 * - each module's own API test file and web feature folder, run alone after
 *   the whole suites: all of the template's tests pass and none is skipped
 *   (a skip would otherwise hide behind the thousands of other tests).
 * Three modules are generated: a typical one, a one-letter name (the shortest
 * allowed), and the longest name and entity allowed.
 *
 * It also checks that bad input, names that collide with existing code (taken
 * from the copy itself), and names git would ignore are refused without
 * writing anything; that a dry run writes nothing; that entity names are
 * derived only from clear plurals; and that a failed write leaves nothing
 * behind. Fixture names carry a random prefix, so they cannot clash with
 * modules an adopter has generated. A few fixed names (logs, tmp, ...) check
 * the template's own ignore rules: they fail only when those rules hide them,
 * in the template itself, and are skipped where a copy already uses them.
 *
 * Stops at once, naming the files, when any exist that git neither tracks nor
 * ignores (`git add -A` first): the generator edits tracked files to name the
 * files it creates, so a copy without a module's new files fails with import
 * errors that do not say why.
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

import { resultCounts } from "./lib/steps.mjs";
import { ORIGINAL_SLUG } from "./lib/template.mjs";
import { copyTrackedFiles, requireGitRepository, untrackedFilesProblem } from "./lib/tracked-copy.mjs";

const repo = path.resolve(import.meta.dirname, "..");
requireGitRepository(repo, "test:new-module");
// Before any work: see the header for why a copy without the untracked files is no use.
const unstaged = untrackedFilesProblem(repo);
if (unstaged) {
  console.error(`test:new-module: ${unstaged}`);
  process.exit(1);
}
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

function run(dir, cwd, cmd, args, label, extraEnv = {}) {
  // A failing suite prints far more than spawnSync's default 1 MiB buffer.
  const result = spawnSync(cmd, args, {
    cwd: path.join(dir, cwd),
    encoding: "utf8",
    env: { ...env(dir), ...extraEnv },
    maxBuffer: 256 * 1024 * 1024,
  });
  if (result.status !== 0) {
    const output = `${result.stdout ?? ""}${result.stderr ?? ""}${result.error?.message ?? ""}`;
    failures.push(`${label} failed:\n${output.slice(-3000)}`);
  }
  return { ...result, stdout: result.stdout ?? "" };
}

// The whole suites have thousands of tests, so a floor on their total holds while a generated module's own
// tests are all skipped. Each module's tests also run alone: at least the template's count must pass, none skipped.
function ownTests(label, kind, result, minimum) {
  const counts = resultCounts(kind, result.stdout.split("\n"));
  const others = Object.entries(counts ?? {}).filter(([outcome, count]) => outcome !== "passed" && count > 0);
  check(
    counts && counts.passed >= minimum && others.length === 0,
    `${label}: expected at least ${minimum} passed and nothing skipped or failed, got ${counts ? JSON.stringify(counts) : "no result line"}:\n${result.stdout.slice(-800)}`,
  );
}

const dir = copyRepo();
try {
  const generator = path.join(dir, "scripts/new-module.mjs");
  const gen = (args) => spawnSync(process.execPath, [generator, ...args], { cwd: dir, encoding: "utf8", env: gitEnv(dir) });
  const inCopy = (rel) => readFileSync(path.join(dir, rel), "utf8");
  // The files the generator edits at a marker.
  const sweepFile = "apps/api/tests/integration/test_contract_conformance.py";
  const marked = ["apps/api/app/bootstrap.py", "apps/web/src/lib/api/client.ts", sweepFile];
  // Everything the generator writes or edits.
  const snapshot = () =>
    JSON.stringify([
      ...["apps/api/app/modules", "apps/api/tests/integration", "apps/web/src/features", "supabase/migrations"].map(
        (rel) => readdirSync(path.join(dir, rel), { recursive: true }).sort(),
      ),
      ...marked.map(inCopy),
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
  // The template itself, not an initialized copy, whose ignore rules are the adopter's (as in check-placeholders).
  const templateMode = JSON.parse(inCopy("package.json")).name === ORIGINAL_SLUG;

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
    // No clear singular: the entity must be named. Endings such as -oes and
    // -rves are ambiguous (heroes, shoes; scarves, curves), ellipses has two
    // singulars, and canvas and chaos are singular.
    ...[
      "data", "series", "news", "status", "caches", "quizzes", "abuses", "s",
      "volcanoes", "cargoes", "scarves", "selves", "canvas", "ellipses", "chaos",
    ].map((word) => [
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
  // Secret directories are ignored at every depth, so no module can be named after them.
  if (templateMode && !isModule("secrets") && !inBootstrap("secrets") && !routeUsed("secrets")) {
    refusals.push([["secrets", "--entity", `${Prefix}Thing`], /would be ignored by git \(\.gitignore:\d+: secrets\/\)/]);
  }

  const untouched = snapshot();
  for (const [args, reason] of refusals) {
    const refused = gen(args);
    check(
      refused.status === 2 && reason.test(refused.stderr),
      `not refused with ${reason}: ${args.join(" ") || "(none)"} (exit ${refused.status})\n${refused.stderr}`,
    );
  }
  check(snapshot() === untouched, "refused input wrote files");

  // A marker or a file that was removed is refused up front, whichever file holds it, and nothing is written.
  const swept = inCopy(sweepFile);
  const markerLine = "    # new:module:sweep\n";
  check(swept.includes(markerLine), `${sweepFile} has no new:module:sweep marker`);
  writeFileSync(path.join(dir, sweepFile), swept.replace(markerLine, ""));
  const unmarked = gen([fresh("sweep_markers")]);
  rmSync(path.join(dir, sweepFile));
  const missing = gen([fresh("sweep_files")]);
  writeFileSync(path.join(dir, sweepFile), swept);
  check(
    unmarked.status === 2 && unmarked.stderr.includes(`Marker "# new:module:sweep" not found in ${sweepFile}`),
    `a removed marker was not refused (exit ${unmarked.status}):\n${unmarked.stderr}`,
  );
  check(
    missing.status === 2 && missing.stderr.includes(`${sweepFile} does not exist`),
    `a removed marker file was not refused (exit ${missing.status}):\n${missing.stderr}`,
  );
  check(snapshot() === untouched, "a refused marker wrote files");

  // Names that broad ignore rules used to hide are not hidden by the template's
  // anchored rules. Any other refusal (an existing test file, feature, table, or
  // kernel name), and in an initialized copy the adopter's own rules, are skipped.
  for (const name of ["logs", "tmp", "build", "coverage", "test_results"]) {
    if (isModule(name) || inBootstrap(name) || routeUsed(name.replaceAll("_", "-"))) continue;
    const planned = gen([name, "--entity", `${Prefix}Thing`, "--dry-run"]);
    if (planned.status === 0) continue;
    // A refusal exits 2; any other status is the generator failing, never a reason to skip.
    if (planned.status !== 2) {
      failures.push(`"${name}" dry run exited ${planned.status}:\n${planned.stderr}`);
    } else if (templateMode && /would be ignored by git/.test(planned.stderr)) {
      failures.push(`"${name}" is hidden by the template's ignore rules:\n${planned.stderr}`);
    } else {
      console.log(`Skipped "${name}": ${planned.stderr.split("\n")[0]}`);
    }
  }
  // Secret directories stay ignored wherever they are created (REPO-12).
  if (templateMode) {
    const probes = ["secrets/a.json", "perf/secrets/a.json", "apps/api/secrets/a.json", "apps/api/tests/secrets/a.json"];
    const ignored = spawnSync("git", ["check-ignore", "--no-index", "--stdin"], {
      cwd: dir,
      input: probes.join("\n"),
      encoding: "utf8",
      env: gitEnv(dir),
    });
    for (const probe of probes) check(ignored.stdout.split("\n").includes(probe), `.gitignore does not ignore ${probe}`);
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
    heroes: "Hero",
    shoes: "Shoe",
    curves: "Curve",
    archives: "Archive",
    lenses: "Lens",
    responses: "Response",
    oases: "Oasis",
    synopses: "Synopsis",
    parentheses: "Parenthesis",
    paralyses: "Paralysis",
    doses: "Dose",
    eclipses: "Eclipse",
    schemas: "Schema",
    photos: "Photo",
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
    check(/^\s+\d+\. git add -A\b/m.test(result.stdout), `the next steps for ${name} do not tell the adopter to git add -A before pnpm verify`);
  }
  check(/is an existing module/.test(gen([MAIN]).stderr), "generating the same module twice was accepted");

  const uv = (args, label, extraEnv) => run(dir, "apps/api", "uv", ["run", "--frozen", ...args], label, extraEnv);
  uv(["ruff", "check", "."], "ruff check");
  uv(["ruff", "format", "--check", "."], "ruff format");
  uv(["mypy"], "mypy");
  uv(["lint-imports"], "import contracts");
  uv(["squawk", "--config", "../../.squawk.toml", "../../supabase/migrations/*.sql"], "migration lint");

  // The document first, as the next steps tell an adopter: the tests compare every response with it, and the
  // contract sweep looks up each generated operation in it.
  uv(["python", "-m", "app.cli", "openapi", "--output", "../../packages/contracts/openapi.json"], "OpenAPI export");
  run(dir, "apps/web", "node_modules/.bin/openapi-typescript", ["../../packages/contracts/openapi.json", "-o", "src/lib/api/schema.d.ts"], "web contract types");

  // `pnpm test:api`: --require-db makes a database that is down a failure, not a skip, and the Stripe sandbox
  // suite (which would call Stripe) stays off whatever the shell exports.
  const pytest = uv(["pytest", "-q", "-p", "no:cacheprovider", "-rfEs", "--require-db"], "API tests", { STRIPE_SANDBOX_TESTS: "" });
  const perModule = (inCopy("scripts/templates/module/test_module.py.tmpl").match(/^def test_/gm) ?? []).length;
  const passed = Number(/(\d+) passed/.exec(pytest.stdout)?.[1] ?? 0);
  check(
    passed >= modules.length * perModule && !/\b\d+ (failed|errors?)\b/.test(pytest.stdout),
    `API tests did not all run:\n${pytest.stdout.slice(-800)}`,
  );
  for (const [name] of modules) {
    const own = uv(["pytest", "-q", "-p", "no:cacheprovider", "-rfEs", "--require-db", `tests/integration/test_${name}.py`], `API tests of ${name}`, { STRIPE_SANDBOX_TESTS: "" });
    ownTests(`API tests of ${name}`, "pytest", own, perModule);
  }

  run(dir, "apps/web", "node_modules/.bin/tsc", ["--noEmit"], "web typecheck");
  run(dir, "apps/web", "node_modules/.bin/eslint", [".", "--max-warnings=0"], "web lint");
  run(dir, "apps/web", "node_modules/.bin/prettier", ["--check", "."], "web format");
  // Caches off: they would be written into the working tree's node_modules through the symlink.
  const vitest = run(dir, "apps/web", "node_modules/.bin/vitest", ["run", "--no-cache", "--no-fsModuleCache"], "web tests");
  const webPerModule = (inCopy("scripts/templates/module/web/panel.test.tsx.tmpl").match(/^\s*(it|test)\(/gm) ?? []).length;
  const webPassed = Number(/Tests\s+(\d+) passed/.exec(vitest.stdout)?.[1] ?? 0);
  check(
    webPassed >= modules.length * Math.max(1, webPerModule) && !/\b\d+ failed\b/.test(vitest.stdout),
    `web tests did not all run:\n${vitest.stdout.slice(-800)}`,
  );
  for (const [name] of modules) {
    // The trailing slash keeps the one-letter module's filter from matching every folder that starts with it.
    const own = run(dir, "apps/web", "node_modules/.bin/vitest", ["run", "--no-cache", "--no-fsModuleCache", `src/features/${name.replaceAll("_", "-")}/`], `web tests of ${name}`);
    ownTests(`web tests of ${name}`, "vitest", own, Math.max(1, webPerModule));
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`new:module check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(
  "new:module check passed: bad, colliding, and ignored names and removed markers refused; entities derived only from clear plurals; dry run and failed writes leave nothing behind; typical, one-letter, and longest modules pass lint, types, contracts, the whole API suite (including RLS and the contract sweep) and the web lint, types, format and test suite, and each module's own tests, run alone, all ran.",
);
