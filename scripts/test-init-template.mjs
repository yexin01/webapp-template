#!/usr/bin/env node
/**
 * REPO-07 check: runs init-template in temporary copies of the tracked files and
 * asserts that no original identifiers or contact placeholders remain, that
 * .env is created from .env.example only, that re-runs change nothing, that
 * bad input is rejected, and that check-placeholders (DEC-012) passes in
 * template mode and after init but fails for a renamed, unfilled copy.
 * Never touches the working tree.
 */
import { execFileSync, spawnSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const NAME = "Acme Cloud";
const SLUG = "acme-cloud";
const PORT = "55432";
const SECURITY = "security@acme.example";
const CONDUCT = "https://acme.example/conduct";
const CONTACTS = ["--security-contact", SECURITY, "--conduct-contact", CONDUCT];
// The template's own specification and history describe the template itself.
const EXEMPT = [/^dev-docs\//, /^scripts\/(test-)?init-template\.mjs$/, /^scripts\/lib\/template\.mjs$/];

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

function checkPlaceholders(dir) {
  return spawnSync(process.execPath, [path.join(dir, "scripts/check-placeholders.mjs")], {
    env: { PATH: process.env.PATH, TEMPLATE_ROOT: dir },
    encoding: "utf8",
  }).status;
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
  const first = init(dir, ["--name", NAME, "--slug", SLUG, "--db-port", PORT, ...CONTACTS]);
  check(first.status === 0, `init failed: ${first.stderr}`);
  for (const rel of walk(dir)) {
    if (EXEMPT.some((re) => re.test(rel))) continue;
    const text = readFileSync(path.join(dir, rel), "utf8");
    check(!/saas[ _-]?template/i.test(text), `original identifier remains in ${rel}`);
    check(!text.includes("54329"), `original database port remains in ${rel}`);
    check(!/\{\{[A-Z_]+_CONTACT\}\}/.test(text), `contact placeholder remains in ${rel}`);
  }
  const read = (rel) => readFileSync(path.join(dir, rel), "utf8");
  check(JSON.parse(read("package.json")).name === SLUG, "package.json name was not renamed");
  check(read("apps/web/src/components/site-header.tsx").includes(NAME), "site header was not renamed");
  check(read("apps/api/app/http/app.py").includes(`${NAME} API`), "API title was not renamed");
  check(read("docker-compose.yml").includes(`127.0.0.1:${PORT}:5432`), "docker-compose port was not moved");
  check(read(".env.example").includes(`127.0.0.1:${PORT}/app`), ".env.example port was not moved");
  check(existsSync(path.join(dir, ".env")), ".env was not created");
  check(read(".env") === read(".env.example"), ".env must be an exact copy of .env.example");
  check(read("SECURITY.md").includes(`**${SECURITY}**`), "security contact was not filled in");
  check(read("CODE_OF_CONDUCT.md").includes(`**${CONDUCT}**`), "conduct contact was not filled in");
  check(checkPlaceholders(dir) === 0, "check-placeholders failed after init");

  // 2. Re-running is a no-op, with or without the contact options once they are filled in.
  const before = snapshot(dir);
  for (const args of [
    ["--name", NAME, "--slug", SLUG, "--db-port", PORT, ...CONTACTS],
    ["--name", NAME, "--slug", SLUG, "--db-port", PORT],
  ]) {
    const again = init(dir, args);
    const report = again.status === 0 ? JSON.parse(again.stdout.slice(0, again.stdout.lastIndexOf("}") + 1)) : null;
    check(report?.updated.length === 0 && !report.envCreated, `re-run was not idempotent: ${again.stderr.trim()}`);
  }
  check(JSON.stringify(snapshot(dir)) === JSON.stringify(before), "re-run changed files");

  // 3. A dry run changes nothing.
  const dry = copyTrackedFiles();
  dirs.push(dry);
  const pristine = snapshot(dry);
  const dryRun = init(dry, ["--name", NAME, "--slug", SLUG, "--db-port", PORT, ...CONTACTS, "--dry-run"]);
  check(dryRun.status === 0, `dry run failed: ${dryRun.stderr}`);
  check(JSON.stringify(snapshot(dry)) === JSON.stringify(pristine), "dry run changed files");
  check(!existsSync(path.join(dry, ".env")), "dry run created .env");

  check(checkPlaceholders(dry) === 0, "check-placeholders failed in template mode");

  // 4. Invalid input is rejected without changes.
  for (const args of [
    ["--name", NAME, "--slug", "Bad Slug", ...CONTACTS],
    ["--name", 'Bad"Name', "--slug", SLUG, ...CONTACTS],
    ["--name", NAME, "--slug", SLUG, "--db-port", "80", ...CONTACTS],
    ["--slug", SLUG, ...CONTACTS],
    ["--name", NAME, "--slug", SLUG],
    ["--name", NAME, "--slug", SLUG, "--security-contact", SECURITY],
    ["--name", NAME, "--slug", SLUG, "--security-contact", "javascript:alert(1)", "--conduct-contact", CONDUCT],
    ["--name", NAME, "--slug", SLUG, "--security-contact", "a@b", "--conduct-contact", CONDUCT],
    ["--name", NAME, "--slug", SLUG, "--security-contact", "x@y.com](https://evil.example)", "--conduct-contact", CONDUCT],
    ["--name", NAME, "--slug", SLUG, "--security-contact", SECURITY, "--conduct-contact", "http://acme.example"],
  ]) {
    const result = init(dry, args);
    check(result.status === 2, `invalid input was accepted: ${args.join(" ")}`);
  }
  check(JSON.stringify(snapshot(dry)) === JSON.stringify(pristine), "invalid input changed files");

  // 5. A renamed copy that still has placeholders fails check-placeholders.
  const unfilled = copyTrackedFiles();
  dirs.push(unfilled);
  const pkgPath = path.join(unfilled, "package.json");
  writeFileSync(pkgPath, JSON.stringify({ ...JSON.parse(readFileSync(pkgPath, "utf8")), name: SLUG }));
  check(checkPlaceholders(unfilled) === 1, "check-placeholders passed for a renamed copy with placeholders");
} finally {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`init-template check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(
  "init-template check passed: renamed, contacts filled, port moved, .env created from the example, idempotent, dry run safe, bad input rejected, placeholder check enforced.",
);
