/**
 * `pnpm verify`: the one command that runs what CI requires, so "is my change done, and will CI agree?" has
 * one answer. This module holds the step list, the selection, the preflight and the report; scripts/verify.mjs
 * is the entry point. The steps are grouped by CI job (api, web, secrets), in the order CI runs them, and
 * CI calls `node scripts/verify.mjs --job <name>` for its check steps, so the two cannot drift apart.
 *
 * It starts nothing: the database is somebody else's (`pnpm db:up`), and a database that is not there is an
 * error unless `--no-db` says the database tests may not run.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { parseArgs } from "node:util";

import { FAIL, NOT_RUN, PASS, SKIPPED, formatDuration, renderTable, runCommand, runSteps, summarizeOutput } from "./steps.mjs";

export const JOBS = ["api", "web", "secrets", "supabase"];
/** The jobs a plain `pnpm verify` runs. The Supabase image job needs Docker and a large image: ask for it. */
const DEFAULT_JOBS = ["api", "web", "secrets"];

export class UsageError extends Error {}

export const USAGE = `Usage: pnpm verify [options]

Runs what CI requires, in CI's order, and ends with a PASS/FAIL table. Exit status 0 only when no step failed.

  --job <name>   run one CI job: api, web, secrets, or supabase (repeatable; default: api, web, secrets)
  --quick        only lint, format, types and the API and web tests (pnpm verify:quick)
  --no-db        allow the database tests not to run (without it, an unreachable PostgreSQL is an error)
  --offline      skip the steps that need the network (dependency audits)
  --keep-going   do not stop at the first failing step
  --only <id>    run only these steps (repeatable)
  --skip <id>    leave these steps out (repeatable)
  --list         print the steps and exit
  --help         this text

The API tests use TEST_DATABASE_URL if it is set, else the local default of apps/api/tests/conftest.py:
start that one with \`pnpm db:up\` (verify never starts Docker).`;

/** @returns the options, or throws UsageError. */
export function parseArguments(argv) {
  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      options: {
        job: { type: "string", multiple: true, default: [] },
        quick: { type: "boolean", default: false },
        "no-db": { type: "boolean", default: false },
        offline: { type: "boolean", default: false },
        "keep-going": { type: "boolean", default: false },
        only: { type: "string", multiple: true, default: [] },
        skip: { type: "string", multiple: true, default: [] },
        list: { type: "boolean", default: false },
        help: { type: "boolean", default: false },
      },
      allowPositionals: false,
    });
  } catch (error) {
    throw new UsageError(error.message);
  }
  const v = parsed.values;
  for (const job of v.job) {
    if (!JOBS.includes(job)) throw new UsageError(`unknown job "${job}" (jobs: ${JOBS.join(", ")})`);
  }
  return {
    jobs: v.job.length ? [...new Set(v.job)] : DEFAULT_JOBS,
    quick: v.quick,
    noDb: v["no-db"],
    offline: v.offline,
    keepGoing: v["keep-going"],
    only: v.only,
    skip: v.skip,
    list: v.list,
    help: v.help,
  };
}

/** The migrations as paths relative to apps/api, in order (squawk takes files, and no shell expands a glob here). */
function migrationFiles(root) {
  const dir = path.join(root, "supabase/migrations");
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((name) => name.endsWith(".sql"))
    .sort()
    .map((name) => `../../supabase/migrations/${name}`);
}

/**
 * The step list. `quick`: part of `--quick`. `db`: "tests" are the API tests, which skip their database tests
 * unless told to require them; "required" steps cannot run without a database. `network`: needs the network.
 * `needs`: the tools the step uses, which the preflight checks. `summary`: how to read the runner's result line.
 */
export function catalog({ root, migrations = migrationFiles(root) }) {
  const uv = (id, label, args, extra = {}) => ({ id, job: "api", label, command: "uv", args: ["run", ...args], cwd: "apps/api", needs: ["uv"], ...extra });
  const pnpm = (id, label, args, extra = {}) => ({ id, job: "web", label, command: "pnpm", args, cwd: ".", needs: ["pnpm"], ...extra });
  const node = (id, job, label, args, extra = {}) => ({ id, job, label, command: "node", args, cwd: ".", needs: [], ...extra });
  return [
    uv("api-lint", "API: ruff check", ["ruff", "check", "app", "tests", "index.py", "../../perf"], { quick: true }),
    uv("api-format", "API: ruff format --check", ["ruff", "format", "--check", "app", "tests", "index.py", "../../perf"], { quick: true }),
    uv("api-imports", "API: import boundaries (lint-imports)", ["lint-imports"], { quick: true }),
    uv("api-migrations", "API: migration lint (squawk)", ["squawk", "--config", "../../.squawk.toml", ...migrations], { quick: true }),
    uv("api-types", "API: mypy (strict)", ["mypy"], { quick: true }),
    uv("api-tests", "API: pytest", ["pytest"], {
      quick: true,
      db: "tests",
      summary: "pytest",
      // The Stripe sandbox suite calls Stripe when this is 1: the gate runs mocked.
      env: { STRIPE_SANDBOX_TESTS: "" },
    }),
    { id: "api-audit", job: "api", label: "API: dependency audit (locked versions)", command: "sh", args: ["scripts/pip-audit-locked.sh"], cwd: ".", needs: ["uv"], network: true },
    pnpm("web-format", "Web: prettier --check", ["--filter", "web", "format:check"], { quick: true }),
    pnpm("web-lint", "Web: eslint", ["--filter", "web", "lint"], { quick: true }),
    pnpm("web-types", "Web: tsc", ["--filter", "web", "typecheck"], { quick: true }),
    pnpm("web-tests", "Web: vitest", ["--filter", "web", "test"], { quick: true, summary: "vitest" }),
    pnpm("contracts", "API contracts are up to date", ["contracts:check"], { needs: ["pnpm", "uv"] }),
    pnpm("scripts-tests", "Scripts: node --test", ["test:scripts"], { summary: "node-test" }),
    pnpm("init-template", "Template initialization (REPO-07)", ["test:init-template"]),
    pnpm("placeholders", "Community contacts are placeholders (DEC-012)", ["check:placeholders"]),
    pnpm("links", "Documentation links resolve", ["check:links"]),
    pnpm("new-module", "Module generator (ADR-0003)", ["test:new-module"], { db: "required", needs: ["pnpm", "uv"] }),
    pnpm("build", "Web: account-free build and bundle secret scan", ["build:check"]),
    pnpm("web-audit", "Web: dependency audit (production)", ["audit", "--prod"], { network: true }),
    node("licenses-self-test", "web", "Licenses: scanner self-test", ["scripts/check-licenses.mjs", "--self-test"]),
    pnpm("licenses", "Licenses: allowlist", ["security:licenses"], { needs: ["pnpm", "uv"] }),
    pnpm("e2e", "End-to-end (account-free)", ["test:e2e"], {
      needs: ["pnpm", "uv", "chromium"],
      summary: "playwright",
      // Whatever the shell exports, this is the account-free tier; `pnpm verify:sandbox` runs the other.
      env: { E2E_SANDBOX: "" },
    }),
    {
      id: "secrets",
      job: "secrets",
      label: "Secret scan (gitleaks)",
      command: "gitleaks",
      args: ["git", "--config", ".gitleaks.toml", "--redact", "--no-banner"],
      cwd: ".",
      needs: ["gitleaks"],
    },
    node("supabase-image", "supabase", "API on Supabase's PostgreSQL image", ["scripts/test-supabase-image.mjs"], { needs: ["uv", "docker"] }),
  ];
}

/**
 * Which steps run, in catalog order, and why the others do not.
 * @returns {Array<{...step, skip?: string}>}
 */
export function planSteps(steps, options, { gitleaks = true } = {}) {
  const unknown = [...options.only, ...options.skip].filter((id) => !steps.some((step) => step.id === id));
  if (unknown.length) throw new UsageError(`unknown step ${unknown.map((id) => `"${id}"`).join(", ")} (see pnpm verify --list)`);
  const chosen = options.only.length
    ? steps.filter((step) => options.only.includes(step.id))
    : steps.filter((step) => options.jobs.includes(step.job) && (!options.quick || step.quick));
  return chosen.map((step) => {
    let skip;
    if (options.skip.includes(step.id)) skip = "left out with --skip";
    else if (options.offline && step.network) skip = "--offline: this step needs the network";
    else if (options.noDb && step.db === "required") skip = "--no-db: this step needs PostgreSQL";
    else if (step.id === "secrets" && !gitleaks) skip = "gitleaks is not installed (brew install gitleaks); CI scans on every push";
    return skip ? { ...step, skip } : step;
  });
}

/** `pnpm verify --list`. */
export function renderList(steps) {
  return renderTable(
    ["id", "job", "quick", "needs", "command", "note"],
    steps.map((step) => [
      step.id,
      step.job,
      step.quick ? "yes" : "",
      [step.db ? "database" : "", step.network ? "network" : ""].filter(Boolean).join(", "),
      `${step.command} ${step.args.join(" ")}`.slice(0, 80),
      step.skip ?? "",
    ]),
  );
}

/** Host and port of the test database, from TEST_DATABASE_URL or the default in conftest.py. Never the credentials. */
export function databaseEndpoint({ root, env, readFile = (file) => readFileSync(file, "utf8") }) {
  const configured = env.TEST_DATABASE_URL?.trim();
  let source = "TEST_DATABASE_URL";
  let url = configured;
  if (!url) {
    source = "the default in apps/api/tests/conftest.py";
    url = /^DEFAULT_TEST_DATABASE_URL\s*=\s*"([^"]+)"/m.exec(readFile(path.join(root, "apps/api/tests/conftest.py")))?.[1];
  }
  try {
    const parsed = new URL(url ?? "");
    return { host: parsed.hostname.replace(/^\[|\]$/g, ""), port: Number(parsed.port) || 5432, source };
  } catch {
    return { host: "", port: 0, source, invalid: true };
  }
}

/** True when something accepts connections at host:port. */
export function reachable(host, port, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    const done = (ok) => {
      socket.destroy();
      resolve(ok);
    };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

function atLeast(version, minimum) {
  const [major, minor = 0] = version.split(".").map(Number);
  const [minMajor, minMinor = 0] = minimum;
  return major > minMajor || (major === minMajor && minor >= minMinor);
}

/** What the machine is asked: the parameters of the preflight, so tests answer for it. */
export function systemProbe({ root, env }) {
  const ask = (command, args, timeout = 20_000) => spawnSync(command, args, { cwd: root, env, encoding: "utf8", timeout });
  return {
    nodeVersion: process.versions.node,
    exists: (file) => existsSync(file),
    versionOf: (command, args = ["--version"]) => {
      const result = ask(command, args);
      return result.error || result.status !== 0 ? null : result.stdout.trim().split("\n")[0];
    },
    chromiumPath: () => {
      const result = ask("pnpm", ["--filter", "web", "exec", "node", "-e", "console.log(require('@playwright/test').chromium.executablePath())"], 60_000);
      return result.status === 0 ? result.stdout.trim().split("\n").pop() : null;
    },
    reachable,
  };
}

/**
 * What the steps that will run need to exist before any of them runs, each with the command that fixes it.
 * @returns {Promise<Array<{name: string, ok: boolean, fix?: string}>>}
 */
export async function preflight({ root, steps, env, noDb, probe }) {
  const running = steps.filter((step) => !step.skip);
  const needs = new Set(running.flatMap((step) => step.needs ?? []));
  const checks = [];
  const add = (name, ok, fix) => checks.push(ok ? { name, ok } : { name, ok, fix });

  const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
  const minimumNode = /(\d+)\.(\d+)/.exec(manifest.engines?.node ?? "")?.slice(1, 3).map(Number) ?? [22, 12];
  add(
    `Node ${minimumNode.join(".")} or newer (this is ${probe.nodeVersion})`,
    atLeast(probe.nodeVersion, minimumNode),
    "install a current Node (.nvmrc names the version the repository uses), then open a new shell",
  );

  if (needs.has("pnpm")) {
    const wanted = /^pnpm@(\d+)\./.exec(manifest.packageManager ?? "");
    const have = probe.versionOf("pnpm");
    add(
      `pnpm ${wanted ? `${wanted[1]}.x ` : ""}(this is ${have ?? "not installed"})`,
      Boolean(have) && (!wanted || have.startsWith(`${wanted[1]}.`)),
      `corepack enable && corepack prepare ${manifest.packageManager ?? "pnpm@latest"} --activate`,
    );
    add("web dependencies installed (node_modules)", probe.exists(path.join(root, "node_modules")), "pnpm bootstrap");
  }
  if (needs.has("uv")) {
    const have = probe.versionOf("uv");
    add(`uv (this is ${have ?? "not installed"})`, Boolean(have), "install uv: https://docs.astral.sh/uv/getting-started/installation/");
    add("API dependencies installed (apps/api/.venv)", probe.exists(path.join(root, "apps/api/.venv")), "pnpm api:sync");
  }
  if (!noDb && running.some((step) => step.db)) {
    const endpoint = databaseEndpoint({ root, env });
    if (endpoint.invalid) {
      add(
        `PostgreSQL for the tests: ${endpoint.source} is not a URL`,
        false,
        "set TEST_DATABASE_URL=postgresql://user:password@host:port/postgres, or unset it to use the local default",
      );
    } else {
      add(
        `PostgreSQL for the tests is reachable at ${endpoint.host}:${endpoint.port} (${endpoint.source})`,
        await probe.reachable(endpoint.host, endpoint.port),
        "pnpm db:up (needs Docker; verify never starts it), or set TEST_DATABASE_URL to a local server, or accept that the database tests (roughly half of the API tests) do not run: pnpm verify --no-db",
      );
    }
  }
  if (needs.has("chromium")) {
    const executable = probe.chromiumPath();
    add("Chromium for Playwright is installed", Boolean(executable) && probe.exists(executable), "pnpm --filter web exec playwright install chromium");
  }
  return checks;
}

/** Commit, whether the working tree has changes, and the date: what makes a summary usable as evidence. */
export function gather({ root, env }) {
  const git = (args) => {
    const result = spawnSync("git", args, { cwd: root, env, encoding: "utf8" });
    return result.status === 0 ? result.stdout.trim() : null;
  };
  const changed = git(["status", "--porcelain"]);
  return {
    commit: git(["rev-parse", "--short=12", "HEAD"]) ?? "unknown",
    dirty: changed === null ? null : changed.length > 0,
    node: process.versions.node,
    date: new Date().toISOString().slice(0, 10),
  };
}

/** The table and the verdict that end the run. `results` carry the job of each step. */
export function renderSummary({ results, meta, options, totalMs }) {
  const rows = results.map((result) => [result.id, result.job ?? "", result.status, formatDuration(result.ms), result.detail]);
  const count = (status) => results.filter((result) => result.status === status).length;
  const failed = results.filter((result) => result.status === FAIL);
  const skipped = results.filter((result) => result.status === SKIPPED);
  const notRun = count(NOT_RUN);
  const tree = meta.dirty === null ? "working tree unknown" : meta.dirty ? "working tree has uncommitted changes" : "working tree clean";
  const lines = ["", `verify: summary for commit ${meta.commit} (${tree}), Node ${meta.node}, ${meta.date}`, renderTable(["step", "job", "result", "time", "detail"], rows), ""];
  if (failed.length) {
    lines.push(
      `verify: FAIL - ${failed.length} failed (${failed.map((result) => result.id).join(", ")})${notRun ? `, ${notRun} not run` : ""} in ${formatDuration(totalMs)}.`,
      `Fix it, run just that again (pnpm verify ${failed.map((result) => `--only ${result.id}`).join(" ")}${options.noDb ? " --no-db" : ""}), then run pnpm verify in full.`,
    );
  } else {
    const caveats = [];
    if (skipped.length) caveats.push(`${skipped.length} skipped: ${skipped.map((result) => `${result.id} (${result.detail})`).join("; ")}`);
    if (options.noDb) caveats.push("database tests were not required (--no-db)");
    if (options.quick) caveats.push("quick: no build, end-to-end, audit or generator checks");
    lines.push(`verify: PASS - ${count(PASS)} passed${caveats.length ? `; ${caveats.join("; ")}` : ""} in ${formatDuration(totalMs)}.`);
  }
  return lines.join("\n");
}

/**
 * The whole command. Returns the exit status: 0 when no step failed, 1 when one did, 2 for a usage error or a
 * preflight check that failed (nothing ran).
 */
export async function main(
  argv,
  {
    root = path.resolve(import.meta.dirname, "../.."),
    env = process.env,
    log = console.log,
    error = console.error,
    probe = systemProbe({ root, env }),
    run = runCommand,
    now = Date.now,
    meta = () => gather({ root, env }),
  } = {},
) {
  let options;
  try {
    options = parseArguments(argv);
  } catch (problem) {
    if (!(problem instanceof UsageError)) throw problem;
    error(`verify: ${problem.message}\n\n${USAGE}`);
    return 2;
  }
  if (options.help) {
    log(USAGE);
    return 0;
  }

  let steps;
  try {
    steps = planSteps(catalog({ root }), options, { gitleaks: Boolean(probe.versionOf("gitleaks", ["version"])) });
  } catch (problem) {
    if (!(problem instanceof UsageError)) throw problem;
    error(`verify: ${problem.message}`);
    return 2;
  }
  if (options.list) {
    log(renderList(steps));
    return 0;
  }

  const checks = await preflight({ root, steps, env, noDb: options.noDb, probe });
  const broken = checks.filter((check) => !check.ok);
  if (broken.length) {
    error("verify: the machine is not ready, so nothing was run:");
    for (const check of broken) error(`  - ${check.name}\n      fix: ${check.fix}`);
    return 2;
  }

  const ci = env.GITHUB_ACTIONS === "true";
  const start = now();
  const scope = options.only.length ? `only ${options.only.join(", ")}` : `${options.jobs.join(", ")}${options.quick ? ", quick" : ""}`;
  log(`verify: ${steps.filter((step) => !step.skip).length} steps (${scope}); ${checks.length} preflight checks passed.`);
  const results = await runSteps(
    steps.map((step) => ({
      id: step.id,
      label: step.label,
      skip: step.skip,
      summarize: step.summary ? (tail) => summarizeOutput(step.summary, tail) : undefined,
      run: () =>
        run({
          command: step.command,
          args: step.db === "tests" && !options.noDb ? [...step.args, "--require-db"] : step.args,
          cwd: path.join(root, step.cwd),
          env: { ...env, NEXT_TELEMETRY_DISABLED: "1", ...step.env },
        }),
    })),
    { keepGoing: options.keepGoing, now, log, ci },
  );
  const withJobs = results.map((result) => ({ ...result, job: steps.find((step) => step.id === result.id)?.job }));
  log(renderSummary({ results: withJobs, meta: meta(), options, totalMs: now() - start }));
  return results.some((result) => result.status === FAIL) ? 1 : 0;
}
