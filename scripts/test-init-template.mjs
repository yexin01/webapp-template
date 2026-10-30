#!/usr/bin/env node
/**
 * REPO-07 check: runs init-template in temporary copies of the tracked files and
 * asserts that no original identifiers or contact placeholders remain, that
 * .env is created from .env.example only, that re-runs change nothing, that a
 * re-run with a different identity is refused, that bad input (including
 * names that would break generated files) is rejected, that a name with the
 * allowed punctuation yields valid files, and that check-placeholders
 * (DEC-012) passes in template mode and after init but fails for a renamed,
 * unfilled copy. Also that a run that failed part-way can be resumed with the
 * same options, that read-only targets are refused before anything changes,
 * that the Docker Compose project is named after the slug, that a later run in
 * an initialized copy leaves the adopter's own text alone, and that this check
 * itself passes in initialized copies after ordinary edits.
 *
 * Adopter policy: an initialized copy no longer carries the template's own
 * records (the DEC-001 proof, the integration notes, the evidence files) or
 * the template owner's open-decisions regime in AGENTS.md, keeps the
 * specification as inherited reference, drops template-only prose, and still
 * resolves every relative Markdown link; --keep-template-material changes
 * none of that; and a finished copy is never touched by the policy again.
 *
 * In a copy that is already initialized, the renaming checks have nothing to
 * rename; it checks only that re-running init with the copy's own identity
 * changes nothing and that a different identity is refused. An identity edited
 * by hand into one init-template does not accept as input skips those checks.
 * Runs on copies of the tracked files (scripts/lib/tracked-copy.mjs) and never
 * touches the working tree.
 */
import { execFileSync, spawnSync } from "node:child_process";
import {
  chmodSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { devNull, tmpdir } from "node:os";
import path from "node:path";

import { brokenLinks } from "./lib/doc-links.mjs";
import {
  ORIGINAL_NAME,
  ORIGINAL_SLUG,
  SPEC_BANNER_MARKER,
  SPEC_FILE,
  TAKEN_PORTS,
  isValidName,
  isValidSlug,
} from "./lib/template.mjs";
import { copyTrackedFiles as copyTracked } from "./lib/tracked-copy.mjs";

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

const copyTrackedFiles = () => copyTracked(repo, "init-template-");

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
const report = (result) =>
  result.status === 0 ? JSON.parse(result.stdout.slice(0, result.stdout.lastIndexOf("}") + 1)) : null;

const currentSlug = JSON.parse(readFileSync(path.join(repo, "package.json"), "utf8")).name;
if (currentSlug !== ORIGINAL_SLUG) {
  checkInitializedCopy();
  process.exit(0);
}

/** An initialized copy: its own identity is a no-op, and any other is refused. */
function checkInitializedCopy() {
  const currentName = /^API_TITLE = "(.*) API"$/m.exec(readFileSync(path.join(repo, "apps/api/app/bootstrap.py"), "utf8"))?.[1];
  console.log(`Already initialized as ${currentSlug}: the renaming checks have nothing to rename and are skipped.`);
  if (!currentName || currentName === ORIGINAL_NAME) {
    console.log("The API title was not found or not renamed, so the re-run checks are skipped too.");
    return;
  }
  // Such an identity was set by hand after init; init-template would refuse it as input.
  if (!isValidName(currentName) || !isValidSlug(currentSlug)) {
    console.log(
      `The name "${currentName}" or slug "${currentSlug}" is one init-template does not accept as input, so it cannot be passed back to it; the re-run checks are skipped too.`,
    );
    return;
  }
  const dir = copyTrackedFiles();
  try {
    const pristine = snapshot(dir);
    const same = init(dir, ["--name", currentName, "--slug", currentSlug]);
    if (same.status === 2 && /is required: .* still contains/.test(same.stderr)) {
      console.log("Community contact placeholders remain (see `pnpm check:placeholders`); the re-run check is skipped.");
    } else {
      const updated = report(same)?.updated;
      check(
        updated?.length === 0,
        `a re-run with this copy's own identity was not a no-op: ${updated ? `it updated ${updated.join(", ")}` : same.stderr.trim()}`,
      );
    }
    const unchanged = (files) => Object.entries(pristine).every(([rel, text]) => files[rel] === text);
    check(unchanged(snapshot(dir)), "a re-run with this copy's own identity changed tracked files");
    for (const args of [
      ["--name", currentName, "--slug", `${currentSlug}-other`],
      ["--name", `${currentName} Other`, "--slug", currentSlug],
    ]) {
      check(init(dir, args).status === 2, `a re-run with a different identity was not refused: ${args.join(" ")}`);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  if (failures.length) {
    console.error(`init-template check FAILED:\n- ${failures.join("\n- ")}`);
    process.exit(1);
  }
  console.log("init-template check passed: re-running with this copy's identity changes nothing; another identity is refused.");
}

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
  const read = (rel) => (existsSync(path.join(dir, rel)) ? readFileSync(path.join(dir, rel), "utf8") : null);
  check(JSON.parse(read("package.json")).name === SLUG, "package.json name was not renamed");
  check(read("apps/web/src/ui/site-header.tsx").includes(NAME), "site header was not renamed");
  check(read("apps/api/app/bootstrap.py").includes(`${NAME} API`), "API title was not renamed");
  check(read("docker-compose.yml").includes(`127.0.0.1:${PORT}:5432`), "docker-compose port was not moved");
  check(/^name: acme-cloud$/m.test(read("docker-compose.yml")), "the Docker Compose project was not named after the slug");
  check(read(".env.example").includes(`127.0.0.1:${PORT}/app`), ".env.example port was not moved");
  check(existsSync(path.join(dir, ".env")), ".env was not created");
  check(read(".env") === read(".env.example"), ".env must be an exact copy of .env.example");
  check(read("SECURITY.md").includes(`**${SECURITY}**`), "security contact was not filled in");
  check(read("CODE_OF_CONDUCT.md").includes(`**${CONDUCT}**`), "conduct contact was not filled in");
  check(checkPlaceholders(dir) === 0, "check-placeholders failed after init");

  // 1a. The adopter policy: the template's own records are gone, its rules for agents are neutral, links hold.
  for (const gone of ["proofs", "docs-notes"]) check(!existsSync(path.join(dir, gone)), `${gone} was not removed`);
  check(
    readdirSync(path.join(dir, "dev-docs/evidence")).join() === "README.md",
    "dev-docs/evidence should hold only the adopter's README",
  );
  check(existsSync(path.join(dir, "dev-docs/adr/0003-modular-architecture.md")), "the ADRs were removed");
  check(
    read(SPEC_FILE).split(SPEC_BANNER_MARKER).length === 2,
    "the specification does not carry exactly one inherited-reference banner",
  );
  const agents = read("AGENTS.md");
  check(agents.includes("<!-- adopter-agents -->"), "AGENTS.md was not replaced by the adopter version");
  check(
    !/§|\bDEC-\d|open decision|owner's open/i.test(agents),
    "the adopter AGENTS.md still carries the template owner's decision regime",
  );
  for (const [rule, pattern] of [
    ["no remote changes without authorization", /Never\*\* push, publish/],
    ["no secrets", /Never\*\* expose, commit, print, log, or transmit secrets/],
    ["no fabricated evidence", /Never\*\* fabricate/],
    ["no weakened tests", /Never\*\* weaken tests/],
    ["preserve work, ask before destructive operations", /Preserve unrelated work. Ask before destructive/],
  ]) {
    check(pattern.test(agents), `the adopter AGENTS.md lost the rule: ${rule}`);
  }
  const scripts = JSON.parse(read("package.json")).scripts;
  for (const [, command] of agents.matchAll(/pnpm ([a-z][\w:-]*)/g)) {
    check(command in scripts, `the adopter AGENTS.md names pnpm ${command}, which package.json does not define`);
  }
  const broken = brokenLinks(dir);
  check(
    broken.length === 0,
    `links do not resolve in the initialized copy: ${broken.map((b) => `${b.file} -> ${b.target}`).join(", ")}`,
  );

  // 1b. bootstrap.py is written first and package.json last (they mark a run in
  // progress and a finished one); timestamps cannot be out of order.
  const written = report(first)?.updated ?? [];
  const times = Object.fromEntries(written.map((rel) => [rel, statSync(path.join(dir, rel), { bigint: true }).mtimeNs]));
  check(
    written.every((rel) => times[rel] >= times["apps/api/app/bootstrap.py"] && times[rel] <= times["package.json"]),
    "init did not write bootstrap.py first and package.json last",
  );

  // 2. Re-running is a no-op, with or without the contact options once they are filled in.
  const before = snapshot(dir);
  for (const args of [
    ["--name", NAME, "--slug", SLUG, "--db-port", PORT, ...CONTACTS],
    ["--name", NAME, "--slug", SLUG, "--db-port", PORT],
  ]) {
    const again = report(init(dir, args));
    check(again?.updated.length === 0 && !again.envCreated, `re-run was not idempotent: ${JSON.stringify(args)}`);
  }
  check(JSON.stringify(snapshot(dir)) === JSON.stringify(before), "re-run changed files");

  // 2b. A re-run with a different identity is refused, not silently ignored.
  for (const args of [
    ["--name", "Other Cloud", "--slug", SLUG],
    ["--name", NAME, "--slug", "other-cloud"],
    ["--name", NAME, "--slug", SLUG, "--db-port", "55433"],
    ["--name", NAME, "--slug", SLUG, "--security-contact", "security@other.example"],
  ]) {
    const result = init(dir, args);
    check(result.status === 2, `a re-run with a different identity was not refused: ${args.join(" ")}`);
  }
  check(JSON.stringify(snapshot(dir)) === JSON.stringify(before), "a refused re-run changed files");

  // 2c. A copy initialized before init named the Compose project keeps its
  // folder-named project (and database volume), with a notice.
  const compose = path.join(dir, "docker-compose.yml");
  writeFileSync(compose, read("docker-compose.yml").replace(/^name: .*\n\n/m, ""));
  const unnamed = readFileSync(compose, "utf8");
  const older = init(dir, ["--name", NAME, "--slug", SLUG, "--db-port", PORT]);
  check(
    report(older)?.updated.length === 0 && readFileSync(compose, "utf8") === unnamed,
    "re-running init renamed the Compose project of an existing copy",
  );
  check(/left without a project name/.test(older.stdout), "no notice about the unnamed Compose project");
  writeFileSync(compose, before["docker-compose.yml"]);

  // 3. A dry run changes nothing.
  const dry = copyTrackedFiles();
  dirs.push(dry);
  const pristine = snapshot(dry);
  const dryRun = init(dry, ["--name", NAME, "--slug", SLUG, "--db-port", PORT, ...CONTACTS, "--dry-run"]);
  check(dryRun.status === 0, `dry run failed: ${dryRun.stderr}`);
  check(report(dryRun)?.removed.includes("proofs"), "the dry run did not list what init would remove");
  check(JSON.stringify(snapshot(dry)) === JSON.stringify(pristine), "dry run changed files");
  check(!existsSync(path.join(dry, ".env")), "dry run created .env");

  check(checkPlaceholders(dry) === 0, "check-placeholders failed in template mode");

  // 4. Invalid input is rejected without changes.
  for (const args of [
    ["--name", NAME, "--slug", "Bad Slug", ...CONTACTS],
    ["--name", 'Bad"Name', "--slug", SLUG, ...CONTACTS],
    // Would break openapi.json and bootstrap.py, or differ from the generated contract.
    ["--name", "Bad\nName", "--slug", SLUG, ...CONTACTS],
    ["--name", "Café Cloud", "--slug", SLUG, ...CONTACTS],
    ["--name", "Acme {Cloud}", "--slug", SLUG, ...CONTACTS],
    ["--name", "Acme's Cloud", "--slug", SLUG, ...CONTACTS],
    ["--name", "Acme <Cloud>", "--slug", SLUG, ...CONTACTS],
    ["--name", "Acme %s", "--slug", SLUG, ...CONTACTS],
    // Would be renamed again on every re-run.
    ["--name", "SaaS Template Pro", "--slug", SLUG, ...CONTACTS],
    ["--name", NAME, "--slug", "saas-template-pro", ...CONTACTS],
    ["--name", NAME, "--slug", SLUG, "--db-port", "80", ...CONTACTS],
    // Not canonical, or used by the template's own servers (the e2e servers included).
    ...["08000", "01024", "65536", "1e4", "3000", "8000", "8001", "3100", "8100"].map((port) => [
      "--name", NAME, "--slug", SLUG, "--db-port", port, ...CONTACTS,
    ]),
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
  // The e2e servers bind fixed ports; PostgreSQL must never be moved onto one.
  const e2ePorts = [...pristine["apps/web/playwright.config.ts"].matchAll(/^const (?:web|api)Port = (\d+);$/gm)];
  check(e2ePorts.length === 2, "the e2e ports were not found in apps/web/playwright.config.ts");
  for (const [, port] of e2ePorts) check(TAKEN_PORTS.has(port), `init-template accepts the e2e port ${port}`);

  // 5. A name with the allowed punctuation yields valid, consistent files.
  const punctuated = copyTrackedFiles();
  dirs.push(punctuated);
  const PUNCTUATED = "Acme-Cloud 2.0 (Beta+)!";
  const run = init(punctuated, ["--name", PUNCTUATED, "--slug", SLUG, ...CONTACTS]);
  check(run.status === 0, `init with punctuation failed: ${run.stderr}`);
  const readIn = (rel) => readFileSync(path.join(punctuated, rel), "utf8");
  let openapiTitle = null;
  try {
    openapiTitle = JSON.parse(readIn("packages/contracts/openapi.json")).info.title;
  } catch {
    failures.push("openapi.json is not valid JSON after init");
  }
  check(openapiTitle === `${PUNCTUATED} API`, "openapi.json title does not match the name");
  check(readIn("apps/api/app/bootstrap.py").includes(`API_TITLE = "${PUNCTUATED} API"\n`), "bootstrap.py title is wrong");

  // 5b. Once a copy is initialized its text is the adopter's: a later run with the
  // same identity (here to move the port) leaves a mention of the template alone.
  const credit = "\nAcme Cloud started from the SaaS Template (saas-template).\n";
  writeFileSync(path.join(punctuated, "README.md"), readIn("README.md") + credit);
  const later = init(punctuated, ["--name", PUNCTUATED, "--slug", SLUG, "--db-port", PORT]);
  check(
    later.status === 0 && readIn("docker-compose.yml").includes(`127.0.0.1:${PORT}:5432`),
    `a later --db-port run in an initialized copy failed: ${later.stderr}`,
  );
  check(readIn("README.md").endsWith(credit), "a later run with the same identity rewrote the adopter's own text");
  // The policy ran once: the adopter's AGENTS.md, and a folder they made under a removed name, stay.
  writeFileSync(path.join(punctuated, "AGENTS.md"), "# Our own rules\n");
  mkdirSync(path.join(punctuated, "proofs"));
  writeFileSync(path.join(punctuated, "proofs/ours.txt"), "ours\n");
  const again = init(punctuated, ["--name", PUNCTUATED, "--slug", SLUG, "--db-port", PORT]);
  check(
    report(again)?.updated.length === 0 && report(again)?.removed.length === 0,
    "a later run applied the adopter policy again",
  );
  check(
    readIn("AGENTS.md") === "# Our own rules\n" && existsSync(path.join(punctuated, "proofs/ours.txt")),
    "a later run overwrote the adopter's own files",
  );

  // 5c. Template-only prose is dropped with its markers, and links to removed files become plain text.
  const prose = copyTrackedFiles();
  dirs.push(prose);
  const block = "<!-- template-only:start -->\nTEMPLATE ONLY TEXT\n<!-- template-only:end -->\n";
  const evidenceLink = "[the DEC-001 evidence](dev-docs/evidence/dec-001-vercel-python.md)";
  for (const rel of ["README.md", "CONTRIBUTING.md"]) {
    const text = readFileSync(path.join(prose, rel), "utf8");
    writeFileSync(
      path.join(prose, rel),
      `${text}\n${block}\nSee ${evidenceLink} and [the ADRs](dev-docs/adr/0001-approved-recommendations.md).\n`,
    );
  }
  check(init(prose, ["--name", NAME, "--slug", SLUG, ...CONTACTS]).status === 0, "init failed with template-only prose");
  const proseText = ["README.md", "CONTRIBUTING.md"].map((rel) => readFileSync(path.join(prose, rel), "utf8")).join("\n");
  check(!proseText.includes("TEMPLATE ONLY TEXT") && !proseText.includes("template-only:"), "template-only prose or its markers remain");
  check(
    proseText.includes("See the DEC-001 evidence and [the ADRs](dev-docs/adr/0001-approved-recommendations.md)."),
    "a link to removed material was not turned into text, or a link to kept material was",
  );
  check(brokenLinks(prose).length === 0, "links do not resolve after template-only prose was removed");

  // 5d. --keep-template-material keeps all of it: files, prose, and the template's own AGENTS.md.
  const kept = copyTrackedFiles();
  dirs.push(kept);
  const keptAgents = readFileSync(path.join(kept, "AGENTS.md"), "utf8");
  writeFileSync(path.join(kept, "README.md"), `${readFileSync(path.join(kept, "README.md"), "utf8")}\n${block}`);
  const keptRun = init(kept, ["--name", NAME, "--slug", SLUG, ...CONTACTS, "--keep-template-material"]);
  check(
    keptRun.status === 0 && report(keptRun)?.removed.length === 0,
    `init with --keep-template-material removed something: ${keptRun.stderr}`,
  );
  check(existsSync(path.join(kept, "proofs/dec-001/README.md")), "--keep-template-material removed the DEC-001 proof");
  check(readdirSync(path.join(kept, "dev-docs/evidence")).length > 1, "--keep-template-material removed the template's evidence");
  check(readFileSync(path.join(kept, "AGENTS.md"), "utf8") === keptAgents, "--keep-template-material replaced AGENTS.md");
  check(readFileSync(path.join(kept, "README.md"), "utf8").includes("TEMPLATE ONLY TEXT"), "--keep-template-material dropped template-only prose");
  check(
    !readFileSync(path.join(kept, SPEC_FILE), "utf8").includes(SPEC_BANNER_MARKER),
    "--keep-template-material added the inherited-reference banner",
  );
  check(brokenLinks(kept).length === 0, "links do not resolve with --keep-template-material");
  check(checkPlaceholders(kept) === 0, "check-placeholders failed with --keep-template-material");

  // 6. A renamed copy that still has placeholders fails check-placeholders.
  const unfilled = copyTrackedFiles();
  dirs.push(unfilled);
  const pkgPath = path.join(unfilled, "package.json");
  writeFileSync(pkgPath, JSON.stringify({ ...JSON.parse(readFileSync(pkgPath, "utf8")), name: SLUG }));
  check(checkPlaceholders(unfilled) === 1, "check-placeholders passed for a renamed copy with placeholders");

  // 7. A run that failed part-way is resumed by the same options: an older run
  // wrote package.json first, then the other identifier files in turn; the
  // current one writes package.json last.
  const olderOrder = new Set([
    "package.json", "README.md", "apps/api/pyproject.toml", "apps/api/app/__init__.py", "apps/api/app/bootstrap.py",
  ]);
  for (const [label, partial, other] of [
    ["only package.json renamed", (rel) => rel === "package.json", ["--name", NAME, "--slug", "other-cloud"]],
    // Stopped before the web files and openapi.json.
    ["older order, up to bootstrap.py renamed", (rel) => olderOrder.has(rel), ["--name", "Other Cloud", "--slug", SLUG]],
    ["all but package.json renamed", (rel) => rel !== "package.json", ["--name", "Other Cloud", "--slug", SLUG]],
    // The first write of the current order.
    ["only bootstrap.py renamed", (rel) => rel === "apps/api/app/bootstrap.py", ["--name", "Other Cloud", "--slug", SLUG]],
    // Every write but the last one, with the Compose project name carrying the slug.
    ["all but package.json, other slug", (rel) => rel !== "package.json", ["--name", NAME, "--slug", "other-cloud"]],
  ]) {
    const resumed = copyTrackedFiles();
    dirs.push(resumed);
    const full = ["--name", NAME, "--slug", SLUG, "--db-port", PORT, ...CONTACTS];
    const plan = report(init(resumed, [...full, "--dry-run"]));
    // Apply part of the rename by hand, as a run that stopped part-way would have.
    const done = mkdtempSync(path.join(tmpdir(), "init-template-"));
    dirs.push(done);
    cpSync(resumed, done, { recursive: true });
    init(done, full);
    check(plan?.updated.includes("package.json") && plan.updated.length > 3, "the dry run planned no rename");
    for (const rel of plan?.updated.filter(partial) ?? []) {
      writeFileSync(path.join(resumed, rel), readFileSync(path.join(done, rel)));
    }
    const partialState = JSON.stringify(snapshot(resumed));
    check(init(resumed, other).status === 2, `a different identity was accepted after a part-way rename (${label})`);
    check(JSON.stringify(snapshot(resumed)) === partialState, `a refused resume (${label}) changed files`);
    const finish = init(resumed, full);
    check(finish.status === 0, `a part-way rename (${label}) could not be resumed: ${finish.stderr.trim()}`);
    const expected = snapshot(done);
    const actual = snapshot(resumed);
    if (partial("package.json") && !partial("docker-compose.yml")) {
      // Looks like a copy initialized before init named the Compose project: left as it is (2c).
      expected["docker-compose.yml"] = expected["docker-compose.yml"].replace(/^name: .*\n\n/m, "");
    }
    const unfinished = Object.keys(expected).filter((rel) => actual[rel] !== expected[rel]);
    check(unfinished.length === 0, `resuming a part-way rename (${label}) did not finish ${unfinished.join(", ")}`);
  }

  // 8. A read-only target is refused before anything changes (root ignores permissions).
  if (process.getuid?.() !== 0) {
    const locked = copyTrackedFiles();
    dirs.push(locked);
    const bootstrap = path.join(locked, "apps/api/app/bootstrap.py");
    const before = snapshot(locked);
    chmodSync(bootstrap, 0o444);
    const refused = init(locked, ["--name", NAME, "--slug", SLUG, ...CONTACTS]);
    chmodSync(bootstrap, 0o644);
    check(refused.status === 2 && /not writable/.test(refused.stderr), "a read-only target was not refused up front");
    check(JSON.stringify(snapshot(locked)) === JSON.stringify(before), "a refused read-only run changed files");
  }

  // 9. The shared copy helper never reaches back into the repository: a
  // relative link stays inside the copy, and outside links and submodules are refused.
  const fixture = mkdtempSync(path.join(tmpdir(), "tracked-copy-"));
  dirs.push(fixture);
  const git = (...args) =>
    execFileSync("git", args, { cwd: fixture, env: { ...process.env, GIT_CONFIG_GLOBAL: devNull, GIT_CONFIG_NOSYSTEM: "1" } });
  git("init", "-q");
  mkdirSync(path.join(fixture, "docs"));
  writeFileSync(path.join(fixture, "docs/SECURITY.md"), "original\n");
  symlinkSync("docs/SECURITY.md", path.join(fixture, "SECURITY.md"));
  git("add", "-A");
  try {
    const copied = copyTracked(fixture, "tracked-copy-");
    dirs.push(copied);
    check(readlinkSync(path.join(copied, "SECURITY.md")) === "docs/SECURITY.md", "a relative link was not copied as itself");
    writeFileSync(path.join(copied, "SECURITY.md"), "changed in the copy\n");
    check(readFileSync(path.join(fixture, "docs/SECURITY.md"), "utf8") === "original\n", "a copied link wrote into the repository");
  } catch (error) {
    failures.push(`the copy helper failed on a relative link: ${error.message}`);
  }
  const refusedCopy = (label, pattern) => {
    try {
      dirs.push(copyTracked(fixture, "tracked-copy-"));
      failures.push(`the copy helper accepted ${label}`);
    } catch (error) {
      check(pattern.test(error.message), `the copy helper refused ${label} for the wrong reason: ${error.message}`);
    }
  };
  for (const [link, label] of [
    [path.join(fixture, "docs"), "an absolute link"],
    ["../outside", "a link out of the repository"],
    // Inside the repository when resolved from it, but `..` climbs from the copy.
    [`${"../".repeat(30)}${fixture.slice(1)}/docs/SECURITY.md`, "a link that climbs to / and back into the repository"],
    [`../${path.basename(fixture)}/docs/SECURITY.md`, "a link through a folder named like the repository"],
  ]) {
    symlinkSync(link, path.join(fixture, "escape"));
    git("add", "escape");
    refusedCopy(label, /escape is a symbolic link to .+, outside the repository/);
    git("rm", "-q", "--cached", "escape");
    rmSync(path.join(fixture, "escape"));
  }
  // Each link stays inside on its own, but `..` after a link climbs from where that link points.
  mkdirSync(path.join(fixture, "docs/deep"));
  symlinkSync("..", path.join(fixture, "docs/deep/up"));
  symlinkSync(`up/../../${path.basename(fixture)}/docs/SECURITY.md`, path.join(fixture, "docs/deep/escape"));
  git("add", "docs/deep");
  refusedCopy("a chain of links that leads out of the copy", /escape is a symbolic link that leads outside the repository/);
  git("rm", "-q", "-r", "--cached", "docs/deep");
  rmSync(path.join(fixture, "docs/deep"), { recursive: true });
  // Where a link to a file the copy lacks would write cannot be checked.
  symlinkSync("docs/untracked.md", path.join(fixture, "dangling"));
  git("add", "dangling");
  refusedCopy("a link to a file that is not in the copy", /not in the copy/);
  git("rm", "-q", "--cached", "dangling");
  rmSync(path.join(fixture, "dangling"));
  git("update-index", "--add", "--cacheinfo", `160000,${"1".repeat(40)},vendor/lib`);
  refusedCopy("a submodule", /is a git submodule/);

  // 10. In an initialized copy this check re-runs init with the copy's own
  // identity. An ordinary later edit must not fail it, and an identity edited
  // by hand into one init-template refuses as input is skipped, not failed.
  const adopted = copyTrackedFiles();
  dirs.push(adopted);
  check(init(adopted, ["--name", NAME, "--slug", SLUG, ...CONTACTS]).status === 0, "init failed in the adopter copy");
  const isolatedGit = { ...process.env, GIT_CONFIG_GLOBAL: devNull, GIT_CONFIG_NOSYSTEM: "1" };
  execFileSync("git", ["init", "-q"], { cwd: adopted, env: isolatedGit });
  execFileSync("git", ["add", "-A"], { cwd: adopted, env: isolatedGit });
  for (const [label, rel, from, to, expected] of [
    ["a later mention of the template", "README.md", /$/, "\nAcme Cloud started from the SaaS Template.\n", /check passed/],
    ["a scoped package name", "package.json", `"name": "${SLUG}"`, '"name": "@acme/cloud"', /does not accept/],
    ["a package name with an underscore", "package.json", `"name": "${SLUG}"`, '"name": "acme_cloud"', /does not accept/],
    ["an API title with an ampersand", "apps/api/app/bootstrap.py", `"${NAME} API"`, '"Acme Cloud & Co API"', /does not accept/],
    // Read back as "Acme Cloud " (trailing space), which init-template, trimming --name, can never receive.
    ["an API title with two spaces before API", "apps/api/app/bootstrap.py", `"${NAME} API"`, `"${NAME}  API"`, /does not accept/],
  ]) {
    const file = path.join(adopted, rel);
    const original = readFileSync(file, "utf8");
    const edited = original.replace(from, to);
    check(edited !== original, `the adopter-copy edit (${label}) changed nothing`);
    writeFileSync(file, edited);
    const self = spawnSync(process.execPath, [path.join(adopted, "scripts/test-init-template.mjs")], {
      env: isolatedGit,
      encoding: "utf8",
    });
    writeFileSync(file, original);
    check(
      self.status === 0 && expected.test(self.stdout),
      `test:init-template failed in an initialized copy with ${label} (exit ${self.status}):\n${self.stdout}${self.stderr}`,
    );
  }
} finally {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`init-template check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(
  "init-template check passed: renamed, contacts filled, port moved, adopter policy applied once (template records removed or kept on request, neutral AGENTS.md, inherited-reference banner, template-only prose dropped, links resolve), Compose project named (an existing one kept), .env created from the example, idempotent, different re-runs refused, dry run safe, bad input rejected, punctuated names valid, adopter text kept on later runs, placeholder check enforced, part-way runs resumable, read-only targets refused up front, copies never reach back into the repository, initialized copies checked without false failures.",
);
