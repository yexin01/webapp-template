import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { FAIL, NOT_RUN, PASS, SKIPPED } from "../lib/steps.mjs";
import { UsageError, catalog, databaseEndpoint, main, parseArguments, planSteps, preflight, renderList, renderSummary } from "../lib/verify.mjs";

const repo = path.resolve(import.meta.dirname, "../..");
const options = (argv = []) => parseArguments(argv);

test("the defaults are CI's api and web jobs and the secret scan, and every flag is read", () => {
  assert.deepEqual(options(), { jobs: ["api", "web", "secrets"], quick: false, noDb: false, offline: false, keepGoing: false, only: [], skip: [], list: false, help: false });
  const all = options(["--job", "web", "--job", "web", "--job", "supabase", "--quick", "--no-db", "--offline", "--keep-going", "--only", "a", "--skip", "b", "--list"]);
  assert.deepEqual([all.jobs, all.quick, all.noDb, all.offline, all.keepGoing, all.only, all.skip, all.list], [["web", "supabase"], true, true, true, true, ["a"], ["b"], true]);
});

test("an unknown job or option is a usage error that names it", () => {
  assert.throws(() => options(["--job", "deploy"]), (error) => error instanceof UsageError && /unknown job "deploy" \(jobs: api, web, secrets, supabase\)/.test(error.message));
  assert.throws(() => options(["--nope"]), UsageError);
  assert.throws(() => options(["extra"]), UsageError);
});

// The catalog is what CI runs: it must not name a script, file or job that does not exist.
test("every step names a script, file and job that exist", () => {
  const steps = catalog({ root: repo });
  const rootScripts = JSON.parse(readFileSync(path.join(repo, "package.json"), "utf8")).scripts;
  const webScripts = JSON.parse(readFileSync(path.join(repo, "apps/web/package.json"), "utf8")).scripts;
  assert.equal(new Set(steps.map((step) => step.id)).size, steps.length, "step ids are unique");
  for (const step of steps) {
    assert.ok(["api", "web", "secrets", "supabase"].includes(step.job), step.id);
    assert.ok(existsSync(path.join(repo, step.cwd)), `${step.id}: cwd ${step.cwd}`);
    if (step.command === "pnpm") {
      const [first, workspace, script] = step.args;
      if (first === "--filter") assert.ok(workspace === "web" && webScripts[script], `${step.id}: apps/${workspace} has no script ${script}`);
      else if (first !== "audit") assert.ok(rootScripts[first], `${step.id}: package.json has no script ${first}`);
    }
    if (step.command === "node" || step.command === "sh") assert.ok(existsSync(path.join(repo, step.args[0])), `${step.id}: ${step.args[0]}`);
  }
});

test("the API steps are the ones CI runs from apps/api, with the migrations listed one by one", () => {
  const steps = catalog({ root: repo });
  const squawk = steps.find((step) => step.id === "api-migrations");
  assert.deepEqual(squawk.args.slice(0, 5), ["run", "--locked", "squawk", "--config", "../../.squawk.toml"]);
  const migrations = squawk.args.slice(5);
  assert.ok(migrations.length >= 10 && migrations.every((file) => /^\.\.\/\.\.\/supabase\/migrations\/\d+_.+\.sql$/.test(file)));
  assert.deepEqual(migrations, [...migrations].sort());
  for (const file of migrations) assert.ok(existsSync(path.join(repo, "apps/api", file)), file);
  assert.deepEqual(
    steps.filter((step) => step.job === "api").map((step) => step.id),
    ["api-lint", "api-format", "api-imports", "api-migrations", "api-types", "api-tests", "api-audit"],
  );
  assert.ok(steps.filter((step) => step.job === "api" && step.command === "uv").every((step) => step.cwd === "apps/api"));
});

test("the end-to-end step is always the account-free tier, and the API tests never reach Stripe", () => {
  const steps = catalog({ root: repo });
  assert.equal(steps.find((step) => step.id === "e2e").env.E2E_SANDBOX, "");
  assert.equal(steps.find((step) => step.id === "api-tests").env.STRIPE_SANDBOX_TESTS, "");
});

const ids = (steps) => steps.map((step) => step.id);

test("--quick is lint, format, types and the tests, and nothing that builds or audits", () => {
  const quick = planSteps(catalog({ root: repo }), options(["--quick"]));
  assert.deepEqual(ids(quick), ["api-lint", "api-format", "api-imports", "api-migrations", "api-types", "api-tests", "web-format", "web-lint", "web-types", "web-tests"]);
});

test("a plain run has the api and web jobs in CI's order and the secret scan; the Supabase image is opt-in", () => {
  const plain = ids(planSteps(catalog({ root: repo }), options()));
  assert.equal(plain.at(-1), "secrets");
  assert.equal(plain.at(-2), "e2e");
  assert.ok(!plain.includes("supabase-image"));
  assert.ok(plain.indexOf("build") < plain.indexOf("e2e"), "the bundle scan comes before the end-to-end build, as in CI");
  assert.deepEqual(ids(planSteps(catalog({ root: repo }), options(["--job", "supabase"]))), ["supabase-image"]);
  assert.ok(ids(planSteps(catalog({ root: repo }), options(["--job", "web"]))).every((id) => !id.startsWith("api-")));
});

test("--only picks steps whatever their job, and an unknown id is refused", () => {
  assert.deepEqual(ids(planSteps(catalog({ root: repo }), options(["--only", "e2e", "--only", "api-types"]))), ["api-types", "e2e"]);
  assert.throws(() => planSteps(catalog({ root: repo }), options(["--only", "nope"])), /unknown step "nope"/);
  assert.throws(() => planSteps(catalog({ root: repo }), options(["--skip", "nope"])), /unknown step "nope"/);
});

test("steps that do not run say why: --skip, --offline, --no-db, a missing gitleaks", () => {
  const reasons = Object.fromEntries(
    planSteps(catalog({ root: repo }), options(["--skip", "links", "--offline", "--no-db"]), { gitleaks: false })
      .filter((step) => step.skip)
      .map((step) => [step.id, step.skip]),
  );
  assert.deepEqual(reasons, {
    "api-audit": "--offline: this step needs the network",
    links: "left out with --skip",
    "new-module": "--no-db: this step needs PostgreSQL",
    "web-audit": "--offline: this step needs the network",
    secrets: "gitleaks is not installed (brew install gitleaks); CI scans on every push",
  });
  const listing = renderList(planSteps(catalog({ root: repo }), options(["--offline"])));
  assert.match(listing, /api-audit .*--offline: this step needs the network/);
});

function scratchRoot(t, { env = {} } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), "verify-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(path.join(root, "package.json"), JSON.stringify({ engines: { node: ">=22.12" }, packageManager: "pnpm@9.15.0" }));
  mkdirSync(path.join(root, "node_modules"));
  mkdirSync(path.join(root, "apps/api/.venv"), { recursive: true });
  mkdirSync(path.join(root, "apps/api/tests"), { recursive: true });
  writeFileSync(path.join(root, "apps/api/tests/conftest.py"), 'X = 1\nDEFAULT_TEST_DATABASE_URL = "postgresql://user:secret@127.0.0.1:55555/app"\n');
  mkdirSync(path.join(root, "supabase/migrations"), { recursive: true });
  writeFileSync(path.join(root, "supabase/migrations/20260101000001_one.sql"), "select 1;");
  writeFileSync(path.join(root, "supabase/migrations/20260101000002_two.sql"), "select 2;");
  writeFileSync(path.join(root, "supabase/migrations/notes.txt"), "not a migration");
  return { root, env };
}

test("the test database comes from TEST_DATABASE_URL, else from conftest.py, and its credentials are never kept", (t) => {
  const { root } = scratchRoot(t);
  assert.deepEqual(databaseEndpoint({ root, env: {} }), { host: "127.0.0.1", port: 55555, source: "the default in apps/api/tests/conftest.py" });
  assert.deepEqual(databaseEndpoint({ root, env: { TEST_DATABASE_URL: "postgresql://a:b@db.internal:6000/x" } }), { host: "db.internal", port: 6000, source: "TEST_DATABASE_URL" });
  assert.deepEqual(databaseEndpoint({ root, env: { TEST_DATABASE_URL: "postgresql://a:b@[::1]/x" } }), { host: "::1", port: 5432, source: "TEST_DATABASE_URL" });
  assert.equal(databaseEndpoint({ root, env: { TEST_DATABASE_URL: "not a url" } }).invalid, true);
  assert.ok(!JSON.stringify(databaseEndpoint({ root, env: {} })).includes("secret"));
  assert.ok(databaseEndpoint({ root: repo, env: {} }).port > 0, "the real conftest.py has a default the script can read");
});

const healthy = (overrides = {}) => ({
  nodeVersion: "24.1.0",
  exists: () => true,
  versionOf: (command) => ({ pnpm: "9.15.0", uv: "uv 0.11.1", gitleaks: "8.30.0" })[command] ?? null,
  chromiumPath: () => "/browsers/chromium",
  reachable: async () => true,
  stagingProblem: () => null,
  ...overrides,
});
const stepsFor = (root, argv = []) => planSteps(catalog({ root }), options(argv));
const failedChecks = async (root, probe, argv = [], env = {}) =>
  (await preflight({ root, steps: stepsFor(root, argv), env, noDb: argv.includes("--no-db"), probe })).filter((check) => !check.ok);

test("a machine that has everything passes the preflight", async (t) => {
  const { root } = scratchRoot(t);
  assert.deepEqual(await failedChecks(root, healthy()), []);
});

test("an unreachable PostgreSQL fails the preflight with the commands that fix it, and never prints the password", async (t) => {
  const { root } = scratchRoot(t);
  const [check, ...others] = await failedChecks(root, healthy({ reachable: async () => false }));
  assert.equal(others.length, 0);
  assert.match(check.name, /PostgreSQL for the tests is reachable at 127\.0\.0\.1:55555 \(the default in apps\/api\/tests\/conftest\.py\)/);
  assert.match(check.fix, /pnpm db:up/);
  assert.match(check.fix, /TEST_DATABASE_URL/);
  assert.match(check.fix, /pnpm verify --no-db/);
  assert.ok(!JSON.stringify(check).includes("secret"));
});

test("--no-db, or no step that needs a database, skips the database check", async (t) => {
  const { root } = scratchRoot(t);
  let asked = 0;
  const probe = healthy({ reachable: async () => ((asked += 1), false) });
  assert.deepEqual(await failedChecks(root, probe, ["--no-db"]), []);
  assert.deepEqual(await failedChecks(root, probe, ["--only", "web-lint"]), []);
  assert.equal(asked, 0);
  assert.equal((await failedChecks(root, probe, ["--only", "api-tests"])).length, 1);
});

test("a bad TEST_DATABASE_URL is reported as that", async (t) => {
  const { root } = scratchRoot(t);
  const [check] = await failedChecks(root, healthy(), [], { TEST_DATABASE_URL: "nonsense" });
  assert.match(check.name, /TEST_DATABASE_URL is not a URL/);
});

test("each missing tool fails the preflight with its fix", async (t) => {
  const { root } = scratchRoot(t);
  const fixes = async (probe, argv) => (await failedChecks(root, probe, argv)).map((check) => check.fix);
  assert.deepEqual(await fixes(healthy({ nodeVersion: "20.1.0" }), ["--only", "web-lint"]), ["install a current Node (.nvmrc names the version the repository uses), then open a new shell"]);
  assert.deepEqual(await fixes(healthy({ versionOf: () => null }), ["--only", "web-lint"]), ["corepack enable && corepack prepare pnpm@9.15.0 --activate"]);
  // Node 25 and later no longer distribute Corepack.
  assert.deepEqual(await fixes(healthy({ nodeVersion: "25.8.2", versionOf: () => null }), ["--only", "web-lint"]), ["npm install -g pnpm@9.15.0"]);
  assert.deepEqual(await fixes(healthy({ nodeVersion: "24.19.0", versionOf: () => null }), ["--only", "web-lint"]), ["corepack enable && corepack prepare pnpm@9.15.0 --activate"]);
  assert.deepEqual(await fixes(healthy({ versionOf: (command) => (command === "pnpm" ? "10.1.0" : "x") }), ["--only", "web-lint"]), ["corepack enable && corepack prepare pnpm@9.15.0 --activate"]);
  assert.deepEqual(await fixes(healthy({ versionOf: (command) => (command === "uv" ? null : "9.15.0") }), ["--only", "api-types"]), ["install uv: https://docs.astral.sh/uv/getting-started/installation/"]);
  assert.deepEqual(await fixes(healthy({ exists: (file) => !file.endsWith("node_modules") }), ["--only", "web-lint"]), ["pnpm bootstrap"]);
  assert.deepEqual(await fixes(healthy({ exists: (file) => !file.endsWith(".venv") }), ["--only", "api-types"]), ["pnpm api:sync"]);
  assert.deepEqual(await fixes(healthy({ chromiumPath: () => null }), ["--only", "e2e"]), ["pnpm --filter web exec playwright install chromium"]);
  assert.deepEqual(await fixes(healthy({ exists: (file) => file !== "/browsers/chromium" }), ["--only", "e2e"]), ["pnpm --filter web exec playwright install chromium"]);
});

test("a file git does not track yet fails the preflight of the generator check, before any step runs", async (t) => {
  const { root } = scratchRoot(t);
  const problem = "1 file is not in git yet (apps/api/app/modules/gadgets/__init__.py): this check copies the files git tracks, so run `git add -A` (or delete or ignore it) and run the command again.";
  const probe = healthy({ stagingProblem: () => problem });
  const [check, ...others] = await failedChecks(root, probe, ["--only", "new-module"]);
  assert.equal(others.length, 0);
  assert.match(check.name, /git tracks every file the generator check copies/);
  assert.equal(check.fix, problem);
  // Only the step that works on a copy of the tracked files asks.
  assert.deepEqual(await failedChecks(root, probe, ["--only", "web-lint"]), []);
  assert.deepEqual(await failedChecks(root, probe, ["--quick"]), []);
  assert.deepEqual(await failedChecks(root, probe, ["--skip", "new-module"]), []);
  assert.equal((await failedChecks(root, probe)).length, 1, "a plain run asks");
  const run = await runMain(t, [], { probe });
  assert.equal(run.status, 2);
  assert.equal(run.calls.length, 0, "a step ran before the preflight stopped");
  assert.match(run.err, /nothing was run[^]*git tracks every file[^]*fix: 1 file is not in git yet/);
});

test("a tool a skipped step would have needed is not asked for", async (t) => {
  const { root } = scratchRoot(t);
  assert.deepEqual(await failedChecks(root, healthy({ chromiumPath: () => null }), ["--skip", "e2e"]), []);
  assert.deepEqual(await failedChecks(root, healthy({ versionOf: () => null }), ["--job", "secrets"]).then((checks) => checks.map((check) => check.fix.slice(0, 7))), []);
});

const meta = () => ({ commit: "abc123def456", dirty: false, node: "24.1.0", date: "2026-10-30" });
const sink = () => {
  const lines = [];
  return { lines, fn: (line) => void lines.push(String(line)) };
};

/** A run function that answers per step id from a table of exit codes. */
function recorder(codes = {}) {
  const calls = [];
  return {
    calls,
    run: async ({ command, args, cwd, env }) => {
      calls.push({ command, args, cwd, env });
      const id = [...Object.keys(codes)].find((name) => args.join(" ").includes(codes[name].match ?? name));
      const code = id === undefined ? 0 : codes[id].code;
      return { code, tail: id && codes[id].tail ? codes[id].tail : [] };
    },
  };
}

async function runMain(t, argv, { probe = healthy(), codes = {}, env = {} } = {}) {
  const { root } = scratchRoot(t);
  const out = sink();
  const err = sink();
  const { calls, run } = recorder(codes);
  let time = 0;
  const status = await main(argv, { root, env, probe, run, log: out.fn, error: err.fn, now: () => (time += 1000), meta });
  return { status, out: out.lines.join("\n"), err: err.lines.join("\n"), calls, root };
}

test("a passing run exits 0 and ends with the table and PASS", async (t) => {
  const { status, out, calls } = await runMain(t, ["--quick"], { codes: { vitest: { match: "--filter web test", code: 0, tail: ["      Tests  514 passed | 1 skipped (515)"] } } });
  assert.equal(status, 0);
  assert.equal(calls.length, 10);
  assert.match(out, /verify: summary for commit abc123def456 \(working tree clean\), Node 24\.1\.0, 2026-10-30/);
  assert.match(out, /web-tests\s+web\s+PASS\s+\d+\.\ds\s+514 passed \| 1 skipped \(515\)/);
  assert.match(out, /verify: PASS - 10 passed; quick: no build, end-to-end, audit or generator checks in /);
});

test("the first failing step ends the run: exit 1, the rest NOT RUN, and the command to run again", async (t) => {
  const { status, out, calls } = await runMain(t, ["--quick"], { codes: { mypy: { match: "mypy", code: 1 } } });
  assert.equal(status, 1);
  assert.equal(calls.length, 5, "nothing runs after mypy");
  assert.match(out, /api-types\s+api\s+FAIL\s+\d+\.\ds\s+exit 1/);
  assert.match(out, /api-tests\s+api\s+NOT RUN\s+-?\S*\s+an earlier step failed/);
  assert.match(out, /verify: FAIL - 1 failed \(api-types\), 5 not run in /);
  assert.match(out, /pnpm verify --only api-types\), then run pnpm verify in full\./);
});

test("--keep-going runs every step and lists each failure", async (t) => {
  const { status, out, calls } = await runMain(t, ["--quick", "--keep-going"], { codes: { mypy: { match: "mypy", code: 1 }, eslint: { match: "--filter web lint", code: 2 } } });
  assert.equal(status, 1);
  assert.equal(calls.length, 10);
  assert.match(out, /verify: FAIL - 2 failed \(api-types, web-lint\)/);
});

test("the API tests require the database unless --no-db, and a real run never reads the sandbox switch or the Stripe key", async (t) => {
  const strict = await runMain(t, ["--only", "api-tests"], { env: { E2E_SANDBOX: "1", STRIPE_SANDBOX_TESTS: "1" } });
  assert.deepEqual(strict.calls[0].args, ["run", "--locked", "pytest", "--require-db"]);
  assert.equal(strict.calls[0].env.STRIPE_SANDBOX_TESTS, "");
  assert.equal(strict.calls[0].cwd, path.join(strict.root, "apps/api"));
  const lax = await runMain(t, ["--only", "api-tests", "--no-db"], { probe: healthy({ reachable: async () => false }) });
  assert.equal(lax.status, 0);
  assert.deepEqual(lax.calls[0].args, ["run", "--locked", "pytest"]);
  assert.match(lax.out, /verify: PASS - 1 passed; database tests were not required \(--no-db\)/);
  const e2e = await runMain(t, ["--only", "e2e"], { env: { E2E_SANDBOX: "1" } });
  assert.equal(e2e.calls[0].env.E2E_SANDBOX, "");
  assert.equal(e2e.calls[0].env.NEXT_TELEMETRY_DISABLED, "1");
});

test("an unreachable database stops the run before anything starts, and says how to go on", async (t) => {
  const { status, err, calls } = await runMain(t, [], { probe: healthy({ reachable: async () => false }) });
  assert.equal(status, 2);
  assert.equal(calls.length, 0);
  assert.match(err, /verify: the machine is not ready, so nothing was run:/);
  assert.match(err, /fix: pnpm db:up \(needs Docker; verify never starts it\)/);
  assert.match(err, /pnpm verify --no-db/);
});

test("a skipped step beside a passing one is a PASS with the reason in the verdict, so nothing is skipped silently", async (t) => {
  const { status, out } = await runMain(t, ["--only", "api-types", "--only", "secrets"], { probe: healthy({ versionOf: (command) => (command === "gitleaks" ? null : "uv 0.11.1") }) });
  assert.equal(status, 0);
  assert.match(out, /secrets\s+secrets\s+SKIPPED\s+-?\S*\s+gitleaks is not installed/);
  assert.match(out, /verify: PASS - 1 passed; 1 skipped: secrets \(gitleaks is not installed/);
});

test("when every selected step is skipped, nothing was checked: NOTHING RAN and exit 2, never PASS", async (t) => {
  const { status, out } = await runMain(t, ["--job", "secrets"], { probe: healthy({ versionOf: () => null }) });
  assert.equal(status, 2);
  assert.match(out, /secrets\s+secrets\s+SKIPPED\s+-?\S*\s+gitleaks is not installed/);
  assert.match(out, /verify: NOTHING RAN - every selected step was skipped, so nothing was checked: secrets \(gitleaks is not installed/);
  assert.doesNotMatch(out, /verify: PASS/);
  const left = await runMain(t, ["--only", "web-lint", "--skip", "web-lint"]);
  assert.equal(left.status, 2);
  assert.match(left.out, /verify: NOTHING RAN - every selected step was skipped, so nothing was checked: web-lint \(left out with --skip\)/);
  assert.match((await runMain(t, ["--help"])).out, /NOTHING RAN, never PASS/);
});

test("the uv steps assert the lock, and an inherited UV_FROZEN is dropped because uv refuses the two together", async (t) => {
  const uvSteps = catalog({ root: repo }).filter((step) => step.command === "uv");
  assert.ok(uvSteps.length >= 6);
  for (const step of uvSteps) assert.deepEqual(step.args.slice(0, 2), ["run", "--locked"], step.id);
  const { calls } = await runMain(t, ["--only", "api-types", "--only", "web-lint"], { env: { UV_FROZEN: "1", KEPT: "yes" } });
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.env.UV_FROZEN, undefined);
    assert.equal(call.env.KEPT, "yes");
  }
});

test("--list, --help and a usage error run nothing", async (t) => {
  const list = await runMain(t, ["--list", "--job", "web"]);
  assert.equal(list.status, 0);
  assert.equal(list.calls.length, 0);
  assert.match(list.out, /web-format\s+web\s+yes/);
  assert.equal((await runMain(t, ["--help"])).status, 0);
  const bad = await runMain(t, ["--job", "nope"]);
  assert.equal(bad.status, 2);
  assert.match(bad.err, /unknown job "nope"/);
  const unknownStep = await runMain(t, ["--only", "nope"]);
  assert.equal(unknownStep.status, 2);
  assert.match(unknownStep.err, /unknown step "nope"/);
});

test("on GitHub Actions the steps are grouped and a failure is annotated", async (t) => {
  const { out } = await runMain(t, ["--only", "api-types", "--only", "web-lint"], { env: { GITHUB_ACTIONS: "true" }, codes: { eslint: { match: "--filter web lint", code: 1 } } });
  assert.match(out, /::group::API: mypy \(strict\)\n::endgroup::\n::group::Web: eslint\n::endgroup::\n::error title=Web  eslint::failed: exit 1/);
});

test("the summary says what the working tree was, and reads a dirty or unknown one", () => {
  const results = [{ id: "api-lint", job: "api", status: PASS, ms: 1500, detail: "" }, { id: "e2e", job: "web", status: SKIPPED, ms: 0, detail: "left out with --skip" }, { id: "secrets", job: "secrets", status: NOT_RUN, ms: 0, detail: "an earlier step failed" }];
  const dirty = renderSummary({ results, meta: { ...meta(), dirty: true }, options: options(), totalMs: 90_000 });
  assert.match(dirty, /working tree has uncommitted changes/);
  assert.match(dirty, /verify: PASS - 1 passed; 1 skipped: e2e \(left out with --skip\) in 1m30s\./);
  assert.match(renderSummary({ results, meta: { ...meta(), dirty: null }, options: options(), totalMs: 1 }), /working tree unknown/);
  const skippedOnly = renderSummary({ results: [results[1]], meta: meta(), options: options(), totalMs: 1 });
  assert.match(skippedOnly, /verify: NOTHING RAN - every selected step was skipped, so nothing was checked: e2e \(left out with --skip\)\./);
  assert.doesNotMatch(skippedOnly, /PASS/);
  const failing = renderSummary({ results: [{ id: "web-lint", job: "web", status: FAIL, ms: 100, detail: "exit 1" }], meta: meta(), options: options(["--no-db"]), totalMs: 100 });
  assert.match(failing, /pnpm verify --only web-lint --no-db\), then run pnpm verify in full\./);
});
