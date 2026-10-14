#!/usr/bin/env node
/**
 * Generator check (ADR-0003): copies the repository to a temporary directory,
 * runs `new:module` there, and proves the generated module passes the same
 * gates as hand-written code:
 * - API: lint, format, strict types, import contracts, architecture tests,
 *   and the generated integration tests against local PostgreSQL;
 * - contracts: regeneration;
 * - web: types, lint, and the generated component test.
 * It also checks that bad input and names that collide with existing code are
 * refused without writing anything, and that a dry run writes nothing.
 * Never touches the working tree: the API runs in its own virtual environment
 * inside the copy (built from uv.lock and uv's cache), and the web tools reuse
 * node_modules through symlinks with their caches off. Needs `pnpm bootstrap`
 * first.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const failures = [];
const check = (ok, message) => ok || failures.push(message);

function copyRepo() {
  const dir = mkdtempSync(path.join(tmpdir(), "new-module-"));
  const list = (args) => execFileSync("git", args, { cwd: repo, encoding: "utf8" }).split("\0").filter(Boolean);
  const files = new Set([...list(["ls-files", "-z"]), ...list(["ls-files", "-z", "--others", "--exclude-standard"])]);
  for (const rel of files) {
    if (!existsSync(path.join(repo, rel))) continue;
    mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    cpSync(path.join(repo, rel), path.join(dir, rel));
  }
  for (const rel of ["node_modules", "apps/web/node_modules", "packages/contracts/node_modules"]) {
    if (existsSync(path.join(repo, rel))) symlinkSync(path.join(repo, rel), path.join(dir, rel), "dir");
  }
  return dir;
}

// The copy's own virtual environment: `uv run` syncs whichever environment it is given.
const env = (dir) => ({ ...process.env, UV_PROJECT_ENVIRONMENT: path.join(dir, "apps/api/.venv"), TEMPLATE_ROOT: dir });

function run(dir, cwd, cmd, args, label) {
  const result = spawnSync(cmd, args, { cwd: path.join(dir, cwd), encoding: "utf8", env: env(dir) });
  if (result.status !== 0) failures.push(`${label} failed:\n${(result.stdout + result.stderr).slice(-3000)}`);
  return result;
}

const dir = copyRepo();
try {
  const generator = path.join(dir, "scripts/new-module.mjs");
  const gen = (args) => spawnSync(process.execPath, [generator, ...args], { cwd: dir, encoding: "utf8", env: env(dir) });
  // Everything the generator writes or edits.
  const snapshot = () =>
    JSON.stringify([
      ...["apps/api/app/modules", "apps/api/tests/integration", "apps/web/src/features", "supabase/migrations"].map(
        (rel) => readdirSync(path.join(dir, rel), { recursive: true }).sort(),
      ),
      ...["apps/api/app/bootstrap.py", "apps/web/src/lib/api/client.ts"].map((rel) =>
        readFileSync(path.join(dir, rel), "utf8"),
      ),
    ]);

  // Bad input and colliding names are refused with a reason before anything is written.
  const refusals = [
    [[], /must be snake_case/],
    [["Bad-Name"], /must be snake_case/],
    [["data"], /Cannot derive a singular entity name/],
    [["notes", "--entity", "lowercase"], /must be PascalCase/],
    [["class"], /"class" is a Python keyword/],
    [["order", "--entity", "OrderItem"], /"order" is a PostgreSQL reserved word/],
    [["notes", "--entity", "None"], /"None" is a Python keyword/],
    [["passes", "--entity", "Pass"], /"pass" is a Python keyword/],
    [["projects"], /existing module/],
    [["billing"], /existing module/],
    [["probes"], /kernel name/],
    [["kernel"], /reserved/],
    // Locals, fields, methods, and imports of the composition root.
    [["analytics"], /collide with "analytics" in apps\/api\/app\/bootstrap\.py/],
    [["closers"], /collide with "closers" in apps\/api\/app\/bootstrap\.py/],
    [["close", "--entity", "Thing"], /collide with "close" in apps\/api\/app\/bootstrap\.py/],
    [["container", "--entity", "Box"], /collide with "container" in apps\/api\/app\/bootstrap\.py/],
    [["health", "--entity", "Check"], /route \/api\/v1\/health is already used/],
    // FastAPI would rename the existing public schema and break the typed web client.
    ...["plans", "outcomes", "redirects", "problems", "entitlements"].map((n) => [
      [n],
      /is already defined in packages\/contracts\/openapi\.json\. Pass a distinct --entity/,
    ]),
    [["responses"], /"Response" would shadow a name the generated module uses/],
    [["accounts", "--entity", "Org"], /"org_id" would duplicate a field/],
  ];
  const untouched = snapshot();
  for (const [args, reason] of refusals) {
    const refused = gen(args);
    check(
      refused.status === 2 && reason.test(refused.stderr),
      `not refused with ${reason}: ${args.join(" ") || "(none)"} (exit ${refused.status})\n${refused.stderr}`,
    );
  }
  check(snapshot() === untouched, "refused input wrote files");
  check(gen(["time_entries", "--dry-run"]).status === 0, "dry run failed");
  check(snapshot() === untouched, "dry run wrote files");

  // Generate for real.
  const result = gen(["time_entries"]);
  check(result.status === 0, `generator failed: ${result.stderr}`);
  check(gen(["time_entries"]).status === 2, "generating the same module twice was accepted");

  const uv = (args, label) => run(dir, "apps/api", "uv", ["run", "--frozen", ...args], label);
  uv(["ruff", "check", "."], "ruff check");
  uv(["ruff", "format", "--check", "."], "ruff format");
  uv(["mypy"], "mypy");
  uv(["lint-imports"], "import contracts");
  const pytest = uv(
    ["pytest", "-q", "-p", "no:cacheprovider", "-rs", "tests/architecture", "tests/integration/test_time_entries.py"],
    "API tests",
  );
  // Database tests skip when PostgreSQL is down; a skip must not count as a pass.
  const passed = Number(/(\d+) passed/.exec(pytest.stdout)?.[1] ?? 0);
  check(passed >= 16 && !/skipped|failed/.test(pytest.stdout), `API tests did not all run:\n${pytest.stdout.slice(-800)}`);

  uv(["python", "-m", "app.cli", "openapi", "--output", "../../packages/contracts/openapi.json"], "OpenAPI export");
  run(dir, "apps/web", "node_modules/.bin/openapi-typescript", ["../../packages/contracts/openapi.json", "-o", "src/lib/api/schema.d.ts"], "web contract types");
  run(dir, "apps/web", "node_modules/.bin/tsc", ["--noEmit"], "web typecheck");
  run(dir, "apps/web", "node_modules/.bin/eslint", ["src/features/time-entries", "src/lib/api/client.ts", "--max-warnings=0"], "web lint");
  // Caches off: they would be written into the working tree's node_modules through the symlink.
  const vitest = run(
    dir,
    "apps/web",
    "node_modules/.bin/vitest",
    ["run", "--no-cache", "--no-fsModuleCache", "src/features/time-entries"],
    "web component test",
  );
  check(/Tests\s+6 passed/.test(vitest.stdout), `web component tests did not run:\n${vitest.stdout.slice(-800)}`);
} finally {
  rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`new:module check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("new:module check passed: bad and colliding input refused, dry run safe, generated module passes lint, types, contracts, architecture, API and web tests.");
