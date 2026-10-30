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
 * <name> is snake_case and plural: notes, time_entries. The entity name is
 * derived from it only for clear, regular plurals; otherwise pass --entity.
 * The generated code follows the projects module. Names that would collide
 * with existing code, or whose files git would ignore, are refused before
 * anything is written, and a failed write is rolled back.
 * `pnpm test:new-module` checks this generator end to end.
 */
import { spawnSync } from "node:child_process";
import {
  accessSync,
  constants,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");
const templates = path.join(import.meta.dirname, "templates", "module");

const RESERVED = new Set([
  "api", "app", "audit", "billing", "diagnostics", "events", "identity", "jobs", "kernel", "llm", "modules",
  "organizations", "outbox", "projects", "settings", "subscriptions", "users", "webhooks",
  // The alias of app.organization_memberships in the generated row-level security policy.
  "membership",
]);

// -- Singular entity names ----------------------------------------------------------
// Only clear, regular plurals are singularized; anything ambiguous (caches:
// cache or cach? abuses, quizzes, volcanoes, scarves, ellipses, news, series)
// needs --entity instead of a wrong guess.
const IRREGULAR = new Map(
  Object.entries({
    people: "person", children: "child", men: "man", women: "woman", mice: "mouse", geese: "goose", feet: "foot",
    teeth: "tooth", criteria: "criterion", phenomena: "phenomenon", indices: "index", matrices: "matrix",
    vertices: "vertex", appendices: "appendix", analyses: "analysis", crises: "crisis", theses: "thesis",
    hypotheses: "hypothesis", diagnoses: "diagnosis", syntheses: "synthesis", aliases: "alias", biases: "bias",
    gases: "gas", atlases: "atlas", canvases: "canvas", lenses: "lens", oases: "oasis", heroes: "hero",
    potatoes: "potato", tomatoes: "tomato", echoes: "echo", vetoes: "veto", knives: "knife", wives: "wife",
    wolves: "wolf", halves: "half", shelves: "shelf", thieves: "thief", calves: "calf", menus: "menu",
    gurus: "guru", emus: "emu", haikus: "haiku",
    // -sis plurals whose ending regular plurals share (doses, cases, eclipses, cheeses).
    prognoses: "prognosis", neuroses: "neurosis", psychoses: "psychosis", metamorphoses: "metamorphosis",
    symbioses: "symbiosis", emphases: "emphasis", synopses: "synopsis", nemeses: "nemesis",
  }),
);
// Singular words that end in -s (canvas, gas): such a name is not a plural.
const SINGULARS = new Set(IRREGULAR.values());
// Same in both numbers, or with more than one plausible singular.
const NO_CLEAR_SINGULAR = new Set([
  "news", "series", "species", "means", "axes", "bases", "ellipses", "leaves", "lives", "headquarters", "crossroads",
  "physics", "mathematics", "economics", "politics", "ethics", "logistics", "athletics", "linguistics",
  "gymnastics", "diabetes", "measles", "chaos", "kudos", "ethos",
]);
// Singular -us words whose plural adds -es (statuses), unlike abuses or fuses.
const US_WORDS = new Set([
  "status", "bonus", "virus", "campus", "census", "focus", "bus", "syllabus", "prospectus", "apparatus", "nexus",
  "consensus", "surplus", "chorus", "circus", "corpus", "genius", "octopus", "cactus", "walrus", "thesaurus",
  "minus", "plus",
]);
// Singular -ie words whose plural is -ies (movies), unlike categories.
const IE_WORDS = new Set([
  "movie", "cookie", "zombie", "rookie", "calorie", "hoodie", "selfie", "smoothie", "brownie", "goalie", "genie",
  "prairie", "sortie", "freebie", "newbie", "techie", "birdie", "auntie", "lingerie",
]);
// Singular -oe words whose plural adds -s (shoes), unlike heroes or volcanoes.
const OE_WORDS = new Set([
  "shoe", "horseshoe", "snowshoe", "toe", "tiptoe", "canoe", "oboe", "hoe", "foe", "floe", "woe", "aloe",
]);
// Singular -ve words whose plural adds -s (curves) after l, r, oa, ea, or oo,
// where an -f singular is as likely (scarves, selves, loaves, sheaves, hooves).
const VE_WORDS = new Set([
  "curve", "nerve", "reserve", "preserve", "conserve", "serve", "swerve", "valve", "bivalve", "groove", "weave",
]);

/** The singular of one plural word, or undefined when it is not clear. */
function singularOf(word) {
  if (IRREGULAR.has(word)) return IRREGULAR.get(word);
  // Not a regular plural: -ss (class), -us (status), -sis or -xis (analysis, axis), or singular (canvas).
  if (NO_CLEAR_SINGULAR.has(word) || SINGULARS.has(word) || !word.endsWith("s") || /(ss|us|sis|xis)$/.test(word)) {
    return undefined;
  }
  const drop = (n) => word.slice(0, -n) || undefined;
  if (word.endsWith("ies")) {
    // pies, movies; categories; but not -aies or -eies.
    if (word.length <= 4 || IE_WORDS.has(drop(1))) return drop(1);
    return /[^aeiou]ies$/.test(word) ? `${drop(3)}y` : undefined;
  }
  if (/(sses|shes|xes)$/.test(word)) return drop(2); // classes, dishes, boxes
  // matches, beaches; but caches, niches, and headaches end in -che.
  if (word.endsWith("ches")) return /(^|[^aeiou])[aeiy]ches$/.test(word) ? undefined : drop(2);
  if (word.endsWith("uses")) {
    if (US_WORDS.has(drop(2))) return drop(2); // statuses
    return /[ao]uses$/.test(word) ? drop(1) : undefined; // houses, causes; abuses and fuses are unclear
  }
  if (word.endsWith("zzes")) return undefined; // quizzes, buzzes
  if (word.endsWith("oes")) return OE_WORDS.has(drop(1)) ? drop(1) : undefined; // shoes; volcanoes is unclear
  if (/(l|r|oa|ea|oo)ves$/.test(word)) return VE_WORDS.has(drop(1)) ? drop(1) : undefined; // curves; scarves is unclear
  if (/(y|the)ses$/.test(word)) return `${drop(2)}is`; // paralyses, parentheses: no singular ends in -yse or -these
  return drop(1); // notes, cases, invoices, days, waves, archives, responses
}
// Python's keyword.kwlist: such a module cannot be imported, and such an entity cannot be declared.
const PYTHON_KEYWORDS = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del",
  "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
  "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
]);

// PostgreSQL reserved key words: the module name is the table name, which the
// generated policy uses unquoted (`WHERE m.org_id = <name>.org_id`).
const POSTGRES_RESERVED = new Set([
  "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "authorization", "binary", "both",
  "case", "cast", "check", "collate", "collation", "column", "concurrently", "constraint", "create", "cross",
  "current_catalog", "current_date", "current_role", "current_schema", "current_time", "current_timestamp",
  "current_user", "default", "deferrable", "desc", "distinct", "do", "else", "end", "except", "false", "fetch", "for",
  "foreign", "freeze", "from", "full", "grant", "group", "having", "ilike", "in", "initially", "inner", "intersect",
  "into", "is", "isnull", "join", "lateral", "leading", "left", "like", "limit", "localtime", "localtimestamp",
  "natural", "not", "notnull", "null", "offset", "on", "only", "or", "order", "outer", "overlaps", "placing",
  "primary", "references", "returning", "right", "select", "session_user", "similar", "some", "symmetric",
  "system_user", "table", "tablesample", "then", "to", "trailing", "true", "union", "unique", "user", "using",
  "variadic", "verbose", "when", "where", "window", "with",
]);

function fail(message) {
  console.error(message);
  console.error("Usage: pnpm new:module <name> [--entity EntityName] [--dry-run]   e.g. pnpm new:module notes");
  process.exit(2);
}

const read = (rel) => readFileSync(path.join(root, rel), "utf8");
const tree = (rel) => readdirSync(path.join(root, rel), { recursive: true }).filter((f) => !f.includes("__pycache__"));
/** Identifiers in Python source, ignoring comments and string literals. */
const pythonNames = (source) =>
  new Set(source.replace(/("""|''')[\s\S]*?\1|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|#.*/g, " ").match(/\b[A-Za-z_]\w*/g));

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { entity: { type: "string" }, "dry-run": { type: "boolean", default: false } },
});
const name = positionals[0];
const dryRun = values["dry-run"];

if (!name || !/^[a-z][a-z0-9]*(_[a-z0-9]+)*$/.test(name) || name.length > 30) {
  fail("<name> must be snake_case, start with a letter, and be at most 30 characters.");
}
if (PYTHON_KEYWORDS.has(name)) fail(`"${name}" is a Python keyword.`);
if (POSTGRES_RESERVED.has(name)) fail(`"${name}" is a PostgreSQL reserved word, so it cannot name the table.`);
if (existsSync(path.join(root, "apps/api/app/modules", name))) fail(`"${name}" is an existing module.`);
if (tree("apps/api/app/kernel").some((f) => path.basename(f, ".py") === name)) {
  fail(`"${name}" is a kernel name (apps/api/app/kernel).`);
}
if (RESERVED.has(name)) fail(`"${name}" is reserved.`);

const pascal = (snake) => snake.split("_").map((p) => p[0].toUpperCase() + p.slice(1)).join("");
const snake = (pascalName) => pascalName.replace(/(?<!^)([A-Z])/g, "_$1").toLowerCase();

let Entity = values.entity;
if (Entity === undefined) {
  const words = name.split("_");
  const one = singularOf(words.at(-1));
  if (!one) {
    fail(`Cannot derive a singular entity name from "${name}"; pass --entity, e.g. --entity ${pascal(name)}Item.`);
  }
  Entity = pascal([...words.slice(0, -1), one].join("_"));
}
// At least one lowercase letter: an all-caps name (URL) reads as a constant to the linters.
if (!/^[A-Z][A-Za-z0-9]{1,40}$/.test(Entity) || !/[a-z]/.test(Entity)) {
  fail("--entity must be PascalCase with at most 41 characters, e.g. TimeEntry.");
}
for (const id of [Entity, snake(Entity)]) {
  if (PYTHON_KEYWORDS.has(id)) fail(`--entity ${Entity}: "${id}" is a Python keyword.`);
}
const M = pascal(name);
if ([M, `${M}Module`, `${M}Service`, `${M}Repository`, `Pg${M}Repository`].includes(Entity)) {
  fail("The entity name must differ from the module name and its generated classes; pass --entity.");
}

const tokens = {
  __ENTITY__: snake(Entity).toUpperCase(),
  __Entity__: Entity,
  __entity__: snake(Entity),
  __Module__: M,
  __module__: name,
  __path__: name.replaceAll("_", "-"),
  __label__: name.replaceAll("_", " "),
};
const render = (text) => Object.entries(tokens).reduce((out, [token, value]) => out.replaceAll(token, value), text);
const raw = (rel) => readFileSync(path.join(templates, rel), "utf8");
const template = (rel) => render(raw(rel));

// -- Collisions -------------------------------------------------------------------
// Generated names must be new. Otherwise bootstrap.py shadows a local, field,
// or import; FastAPI renames both clashing schemas (breaking `Schemas["X"]` in
// existing web code); or the generated code shadows its own imports. Schema
// names stay readable (`TimeEntry`, as in the projects module) instead of being
// namespaced, so a clash is refused and the user picks a distinct --entity.

const wiring = pythonNames(read("apps/api/app/bootstrap.py"));
for (const id of [name, `build_${name}`, `${M}Module`]) {
  if (wiring.has(id)) fail(`"${name}" would collide with "${id}" in apps/api/app/bootstrap.py; pick another name.`);
}
const contract = JSON.parse(read("packages/contracts/openapi.json"));
if (Object.keys(contract.paths).some((p) => p.split("/")[3] === tokens.__path__)) {
  fail(`The route /api/v1/${tokens.__path__} is already used (packages/contracts/openapi.json); pick another name.`);
}
const feature = `apps/web/src/features/${tokens.__path__}`;
if (existsSync(path.join(root, feature))) fail(`${feature} already exists; pick another name.`);

const defined = new Map();
for (const schema of Object.keys(contract.components?.schemas ?? {})) {
  defined.set(schema.replace(/-(Input|Output)$/, "").split("__").at(-1), "packages/contracts/openapi.json");
}
for (const file of tree("apps/api/app").filter((f) => f.endsWith(".py"))) {
  for (const [, cls] of read(`apps/api/app/${file}`).matchAll(/^\s*class (\w+)/gm)) {
    if (!defined.has(cls)) defined.set(cls, `apps/api/app/${file}`);
  }
}
const clash = (reason) => fail(`${reason}. Pass a distinct --entity, e.g. --entity ${Entity}Item.`);
const schemas = [Entity, `${Entity}Page`, `${Entity}Create`];
for (const id of schemas) {
  if (defined.has(id)) clash(`"${id}" is already defined in ${defined.get(id)}`);
}
// Names the generated module imports or declares itself, such as Response, Field, or Organization.
const used = new Set(
  readdirSync(path.join(templates, "api"))
    .flatMap((f) => [...pythonNames(raw(`api/${f}`))])
    .filter((id) => !/__\w+__/.test(id)),
);
for (const id of [...schemas, `${Entity}Created`, `${Entity}Record`]) {
  if (used.has(id)) clash(`"${id}" would shadow a name the generated module uses`);
}
if (pythonNames(raw("api/domain.py.tmpl")).has(`${tokens.__entity__}_id`)) {
  clash(`"${tokens.__entity__}_id" would duplicate a field of the ${Entity}Created event`);
}
const client = read("apps/web/src/lib/api/client.ts");
const hasMethod = (id) => new RegExp(`\\b${id}\\s*:`).test(client);
if (hasMethod(`list${M}`)) fail(`apps/web/src/lib/api/client.ts already has list${M}; pick another name.`);
for (const id of [`create${Entity}`, `delete${Entity}`]) {
  if (hasMethod(id)) clash(`apps/web/src/lib/api/client.ts already has ${id}`);
}

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

// A generated file that git ignores would silently stay out of commits and CI.
// Skipped when the copy is not a git checkout or git is unavailable.
const ignoreCheck = spawnSync("git", ["check-ignore", "--verbose", "-z", "--stdin"], {
  cwd: root,
  input: Object.keys(files).join("\0"),
  encoding: "utf8",
});
if (ignoreCheck.status === 0) {
  const fields = ignoreCheck.stdout.split("\0");
  for (let i = 0; i + 3 < fields.length; i += 4) {
    const [source, line, pattern, rel] = fields.slice(i, i + 4);
    // A matching "!" pattern re-includes the path.
    if (!pattern.startsWith("!")) {
      fail(`${rel} would be ignored by git (${source}:${line}: ${pattern}). Pick another name, or fix the ignore rule.`);
    }
  }
}

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

// Every target directory must be writable before anything is written: files are
// written to a temporary name and renamed into place, so that takes write access
// to the nearest existing directory of each new file and to the directory of
// each edited file.
const nearestDirectory = (rel) => {
  let dir = path.dirname(path.join(root, rel));
  while (!existsSync(dir)) dir = path.dirname(dir);
  return dir;
};
for (const target of new Set([...[...Object.keys(files), ...edited.keys()].map(nearestDirectory)])) {
  let writable = statSync(target).isDirectory();
  try {
    accessSync(target, constants.W_OK | constants.X_OK);
  } catch {
    writable = false;
  }
  if (!writable) fail(`${path.relative(root, target) || "."} is not a writable directory; nothing was written.`);
}

console.log(JSON.stringify({ dryRun, module: name, entity: Entity, create: Object.keys(files), update: [...edited.keys()] }, null, 2));
if (dryRun) process.exit(0);

// -- Write ------------------------------------------------------------------------
// Each file is written to a temporary name and renamed into place. If anything
// fails, every created file and directory is removed and every edited file is
// restored, so a failed run can simply be repeated. A temporary file left by an
// interrupted run (or a concurrent one) makes the write fail, and is not removed.

const createdDirs = [];
const created = [];
const restore = [];
const temporaries = new Set();
const writeAtomically = (file, text) => {
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.new-module.tmp`);
  writeFileSync(temporary, text, { flag: "wx" });
  temporaries.add(temporary);
  renameSync(temporary, file);
  temporaries.delete(temporary);
};
try {
  for (const [rel, text] of Object.entries(files)) {
    const file = path.join(root, rel);
    const first = mkdirSync(path.dirname(file), { recursive: true });
    if (first) createdDirs.push(first);
    writeAtomically(file, text);
    created.push(file);
  }
  for (const [rel, text] of edited) {
    const file = path.join(root, rel);
    const original = readFileSync(file, "utf8");
    writeAtomically(file, text);
    restore.push([file, original]);
  }
} catch (error) {
  const leftovers = [];
  const attempt = (label, action) => {
    try {
      action();
    } catch {
      leftovers.push(label);
    }
  };
  for (const [file, original] of restore) attempt(path.relative(root, file), () => writeFileSync(file, original));
  for (const file of [...created, ...temporaries]) attempt(path.relative(root, file), () => rmSync(file, { force: true }));
  for (const dir of createdDirs.reverse()) {
    attempt(path.relative(root, dir), () => rmSync(dir, { recursive: true, force: true }));
  }
  console.error(`Writing the module failed: ${error.message}`);
  console.error(
    leftovers.length
      ? `Could not roll back, remove or restore by hand: ${leftovers.join(", ")}`
      : "Every change was rolled back; fix the cause and run the command again.",
  );
  process.exit(1);
}

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
     A new page requires sign-in; only paths listed in apps/web/src/lib/page-access.ts are public.
  5. Replace the placeholder description in ${api}/__init__.py and model your real fields.`);
