#!/usr/bin/env node
/**
 * REPO-07 check: runs init-template in temporary copies of the tracked files and
 * asserts that no original identifiers remain, that .env is created from
 * .env.example only, that re-runs change nothing, and that bad input is
 * rejected. Never touches the working tree.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const NAME = "Acme Cloud";
const SLUG = "acme-cloud";
const PORT = "55432";
// The template's own specification and history describe the template itself.
const EXEMPT = [/^dev-docs\//, /^scripts\/(test-)?init-template\.mjs$/];

const failures = [];
const check = (condition, message) => {
  if (!condition) failures.push(message);
};

function copyTrackedFiles() {
  const dir = mkdtempSync(path.join(tmpdir(), "init-template-"));
  const files = execFileSync("git", ["ls-files", "-z"], { cwd: repo, encoding: "utf8" }).split("\0").filter(Boolean);
  for (const rel of files) {
    const target = path.join(dir, rel);
    mkdirSync(path.dirname(target), { recursive: true });
    cpSync(path.join(repo, rel), target);
  }
  return dir;
}

function init(dir, args) {
  return spawnSync(process.execPath, [path.join(dir, "scripts/init-template.mjs"), ...args], {
    env: { PATH: process.env.PATH, TEMPLATE_ROOT: dir },
    encoding: "utf8",
  });
}

function walk(dir, base = dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    return statSync(full).isDirectory() ? walk(full, base) : [path.relative(base, full)];
  });
}

const snapshot = (dir) => Object.fromEntries(walk(dir).map((rel) => [rel, readFileSync(path.join(dir, rel), "utf8")]));

const dirs = [];
try {
  // 1. A real run renames everything and creates .env from the example.
  const dir = copyTrackedFiles();
  dirs.push(dir);
  const first = init(dir, ["--name", NAME, "--slug", SLUG, "--db-port", PORT]);
  check(first.status === 0, `init failed: ${first.stderr}`);
  for (const rel of walk(dir)) {
    if (EXEMPT.some((re) => re.test(rel))) continue;
    const text = readFileSync(path.join(dir, rel), "utf8");
    check(!/saas[ _-]?template/i.test(text), `original identifier remains in ${rel}`);
    check(!text.includes("54329"), `original database port remains in ${rel}`);
  }
  const read = (rel) => readFileSync(path.join(dir, rel), "utf8");
  check(JSON.parse(read("package.json")).name === SLUG, "package.json name was not renamed");
  check(read("apps/web/src/components/site-header.tsx").includes(NAME), "site header was not renamed");
  check(read("apps/api/app/http/app.py").includes(`${NAME} API`), "API title was not renamed");
  check(read("docker-compose.yml").includes(`127.0.0.1:${PORT}:5432`), "docker-compose port was not moved");
  check(read(".env.example").includes(`127.0.0.1:${PORT}/app`), ".env.example port was not moved");
  check(existsSync(path.join(dir, ".env")), ".env was not created");
  check(read(".env") === read(".env.example"), ".env must be an exact copy of .env.example");

  // 2. Re-running is a no-op.
  const before = snapshot(dir);
  const second = init(dir, ["--name", NAME, "--slug", SLUG, "--db-port", PORT]);
  const report = JSON.parse(second.stdout.slice(0, second.stdout.lastIndexOf("}") + 1));
  check(second.status === 0 && report.updated.length === 0 && !report.envCreated, "re-run was not idempotent");
  check(JSON.stringify(snapshot(dir)) === JSON.stringify(before), "re-run changed files");

  // 3. A dry run changes nothing.
  const dry = copyTrackedFiles();
  dirs.push(dry);
  const pristine = snapshot(dry);
  const dryRun = init(dry, ["--name", NAME, "--slug", SLUG, "--db-port", PORT, "--dry-run"]);
  check(dryRun.status === 0, `dry run failed: ${dryRun.stderr}`);
  check(JSON.stringify(snapshot(dry)) === JSON.stringify(pristine), "dry run changed files");
  check(!existsSync(path.join(dry, ".env")), "dry run created .env");

  // 4. Invalid input is rejected without changes.
  for (const args of [
    ["--name", NAME, "--slug", "Bad Slug"],
    ["--name", 'Bad"Name', "--slug", SLUG],
    ["--name", NAME, "--slug", SLUG, "--db-port", "80"],
    ["--slug", SLUG],
  ]) {
    const result = init(dry, args);
    check(result.status === 2, `invalid input was accepted: ${args.join(" ")}`);
  }
  check(JSON.stringify(snapshot(dry)) === JSON.stringify(pristine), "invalid input changed files");
} finally {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`init-template check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("init-template check passed: renamed, port moved, .env created from the example, idempotent, dry run safe, bad input rejected.");
