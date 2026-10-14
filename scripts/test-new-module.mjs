#!/usr/bin/env node
/**
 * Generator check (ADR-0003): copies the repository to a temporary directory,
 * runs `new:module` there, and proves the generated module passes the same
 * gates as hand-written code:
 * - API: lint, format, strict types, import contracts, architecture tests,
 *   and the generated integration tests against local PostgreSQL;
 * - contracts: regeneration;
 * - web: types, lint, and the generated component test.
 * It also checks that bad input is rejected and that a dry run writes nothing.
 * Never touches the working tree. Dependencies are reused through symlinks,
 * so it needs `pnpm bootstrap` first.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from "node:fs";
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

function run(dir, cwd, cmd, args, label) {
  const result = spawnSync(cmd, args, {
    cwd: path.join(dir, cwd),
    encoding: "utf8",
    env: { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(repo, "apps/api/.venv"), TEMPLATE_ROOT: dir },
  });
  if (result.status !== 0) failures.push(`${label} failed:\n${(result.stdout + result.stderr).slice(-3000)}`);
  return result;
}

const dir = copyRepo();
try {
  const generator = path.join(dir, "scripts/new-module.mjs");
  const gen = (args) =>
    spawnSync(process.execPath, [generator, ...args], { cwd: dir, encoding: "utf8", env: { ...process.env, TEMPLATE_ROOT: dir } });

  // Bad input is rejected; a dry run writes nothing.
  for (const args of [[], ["Bad-Name"], ["projects"], ["billing"], ["data"], ["notes", "--entity", "lowercase"]]) {
    check(gen(args).status === 2, `invalid input was accepted: ${args.join(" ") || "(none)"}`);
  }
  const before = readFileSync(path.join(dir, "apps/api/app/bootstrap.py"), "utf8");
  check(gen(["time_entries", "--dry-run"]).status === 0, "dry run failed");
  check(!existsSync(path.join(dir, "apps/api/app/modules/time_entries")), "dry run created files");
  check(readFileSync(path.join(dir, "apps/api/app/bootstrap.py"), "utf8") === before, "dry run edited bootstrap.py");

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
  const vitest = run(dir, "apps/web", "node_modules/.bin/vitest", ["run", "src/features/time-entries"], "web component test");
  check(/Tests\s+3 passed/.test(vitest.stdout), `web component tests did not run:\n${vitest.stdout.slice(-800)}`);
} finally {
  rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`new:module check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("new:module check passed: bad input rejected, dry run safe, generated module passes lint, types, contracts, architecture, API and web tests.");
