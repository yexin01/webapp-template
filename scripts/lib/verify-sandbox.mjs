/**
 * `pnpm verify:sandbox`: the owner's one command after pasting sandbox keys into .env. It runs the three
 * checks that need provider accounts, each only when its keys are there, says plainly which it skipped and
 * what to paste to enable it, and ends with a spec section 20 evidence record to keep under dev-docs/evidence/:
 *
 *   setup   `pnpm check:setup`: the integrations the .env puts in sandbox mode, checked the way /integrations does
 *   stripe  `pnpm test:stripe-sandbox`: the billing adapter and synchronization against real Stripe test-mode objects
 *   e2e     `E2E_SANDBOX=1 pnpm test:e2e`: the signed-in browser flows against a real Clerk development instance
 *
 * With nothing configured every part is SKIPPED and the exit status is 0: nothing was verified, and the record
 * says NOT VERIFIED. A part whose keys are there but cannot run (the database is down) is BLOCKED, and a part
 * that ran and failed is FAIL: both exit 1. Values are never printed; the record names variables and counts.
 */
import path from "node:path";
import { parseArgs } from "node:util";

import { clerkKeyState } from "./clerk.mjs";
import { effectiveEnv, readDotenv } from "./env.mjs";
import { FAIL, PASS, SKIPPED, formatDuration, renderTable, runCommand, summarizeOutput } from "./steps.mjs";
import { UsageError, databaseEndpoint, gather, systemProbe } from "./verify.mjs";

export const BLOCKED = "BLOCKED";
export const PARTS = ["setup", "stripe", "e2e"];

export const USAGE = `Usage: pnpm verify:sandbox [options]

Runs the checks that need provider accounts, each only when its keys are in .env, and ends with an evidence
record (spec section 20) to paste into a new file under dev-docs/evidence/. Exit status 0 unless a part failed
or could not run; with nothing configured every part is skipped, and nothing is verified.

  --send-test-events   also send one PostHog event and one Sentry issue (sandbox projects), as check:setup does
  --skip <part>        leave a part out: ${PARTS.join(", ")} (repeatable)
  --help               this text

Parts: setup = pnpm check:setup; stripe = pnpm test:stripe-sandbox (set STRIPE_SANDBOX_TESTS=0 to leave it out);
e2e = E2E_SANDBOX=1 pnpm test:e2e.`;

export function parseArguments(argv) {
  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      options: {
        "send-test-events": { type: "boolean", default: false },
        skip: { type: "string", multiple: true, default: [] },
        help: { type: "boolean", default: false },
      },
      allowPositionals: false,
    });
  } catch (error) {
    throw new UsageError(error.message);
  }
  const { skip, help, "send-test-events": sendTestEvents } = parsed.values;
  const unknown = skip.filter((part) => !PARTS.includes(part));
  if (unknown.length) throw new UsageError(`unknown part "${unknown[0]}" (parts: ${PARTS.join(", ")})`);
  return { sendTestEvents, skip, help };
}

/** The integrations the .env sets up, from names only. Modes default as the API's local settings do. */
export function configuration(env) {
  const state = clerkKeyState(env);
  const both = Boolean(env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim() && env.CLERK_SECRET_KEY?.trim());
  const live = /^pk_live_/.test(env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "") || /^sk_live_/.test(env.CLERK_SECRET_KEY ?? "");
  let clerk = "not configured";
  if (state === "missing" || (state === "configured" && !both)) clerk = "not set";
  else if (state === "configured") clerk = live ? "production keys" : "sandbox";
  return {
    clerk,
    billing: env.BILLING_MODE?.trim() || "mock",
    llm: env.LLM_MODE?.trim() || "mock",
    analytics: env.ANALYTICS_MODE?.trim() || "disabled",
    errors: env.ERROR_REPORTING_MODE?.trim() || "disabled",
  };
}

const isLoopback = (host) => host === "localhost" || host === "::1" || /^127(?:\.\d{1,3}){3}$/.test(host);

/** Where a DATABASE_URL points: host and port only, never the credentials. */
function endpointOf(url) {
  try {
    const parsed = new URL(url);
    return { host: parsed.hostname.replace(/^\[|\]$/g, ""), port: Number(parsed.port) || 5432 };
  } catch {
    return null;
  }
}

/**
 * What each part will do: run, be skipped because its keys are missing (SKIPPED), or be unable to run although
 * they are there (BLOCKED). Reasons name variables and commands, never values.
 */
export async function planParts({ root, env, hasDotenv, options, probe }) {
  const mode = configuration(env);
  const parts = [];

  // setup: needs a .env that sets up something beyond the placeholders and mocks.
  const sandboxModes = Object.entries({ billing: mode.billing, llm: mode.llm, analytics: mode.analytics, errors: mode.errors }).filter(([, value]) => value === "sandbox");
  const setup = { id: "setup", name: "Setup check", command: `pnpm check:setup${options.sendTestEvents ? " --send-test-events" : ""}`, mode: "per integration: mock, sandbox or disabled (the table below says which)" };
  if (options.skip.includes("setup")) Object.assign(setup, { status: SKIPPED, reason: "left out with --skip" });
  else if (!hasDotenv) Object.assign(setup, { status: SKIPPED, need: "env", reason: "there is no .env file yet (pnpm init-template creates one, or cp .env.example .env)" });
  else if (mode.clerk !== "sandbox" && !sandboxModes.length) {
    Object.assign(setup, { status: SKIPPED, need: "keys", reason: "nothing is in sandbox mode yet: no Clerk development keys, and BILLING_MODE, LLM_MODE, ANALYTICS_MODE and ERROR_REPORTING_MODE are not sandbox" });
  }
  parts.push(setup);

  // stripe: an unrestricted sandbox key and the Pro prices and product; STRIPE_SANDBOX_TESTS=0 leaves it out.
  const key = env.STRIPE_SECRET_KEY?.trim() ?? "";
  const stripe = { id: "stripe", name: "Stripe sandbox suite", command: "pnpm test:stripe-sandbox --require-db", mode: "sandbox (Stripe test mode)" };
  if (options.skip.includes("stripe")) Object.assign(stripe, { status: SKIPPED, reason: "left out with --skip" });
  else if (["0", "false"].includes((env.STRIPE_SANDBOX_TESTS ?? "").trim().toLowerCase())) Object.assign(stripe, { status: SKIPPED, reason: "STRIPE_SANDBOX_TESTS=0 leaves it out" });
  else if (!key) Object.assign(stripe, { status: SKIPPED, need: "stripe", reason: "STRIPE_SECRET_KEY is not set (a Stripe sandbox key, sk_test_...)" });
  else if (key.startsWith("rk_test_")) Object.assign(stripe, { status: SKIPPED, need: "stripe", reason: "STRIPE_SECRET_KEY is a restricted key; the suite needs an unrestricted sandbox secret key (sk_test_...) for test clocks" });
  else if (!key.startsWith("sk_test_")) Object.assign(stripe, { status: SKIPPED, need: "stripe", reason: "STRIPE_SECRET_KEY is not a sandbox secret key (sk_test_...)" });
  else if (!/^price_/.test(env.STRIPE_PRICE_PRO_MONTHLY ?? "") || !/^price_/.test(env.STRIPE_PRICE_PRO_YEARLY ?? "") || !/^prod_/.test(env.STRIPE_PRODUCT_PRO ?? "")) {
    Object.assign(stripe, { status: SKIPPED, need: "stripe", reason: "STRIPE_PRICE_PRO_MONTHLY, STRIPE_PRICE_PRO_YEARLY and STRIPE_PRODUCT_PRO are not all set (pnpm setup:stripe creates the prices and the product and prints the lines)" });
  } else {
    const endpoint = databaseEndpoint({ root, env });
    if (endpoint.invalid || !(await probe.reachable(endpoint.host, endpoint.port))) {
      Object.assign(stripe, { status: BLOCKED, need: "database", reason: `the test database is not reachable (${endpoint.invalid ? "TEST_DATABASE_URL is not a URL" : `${endpoint.host}:${endpoint.port}`}): pnpm db:up, or set TEST_DATABASE_URL to a local server` });
    }
  }
  parts.push(stripe);

  // e2e: Clerk development keys, the .env database up (and local: the run writes test rows to it), Chromium.
  const e2e = {
    id: "e2e",
    name: "Sandbox end-to-end",
    command: "E2E_SANDBOX=1 pnpm test:e2e",
    mode: `sandbox (Clerk development instance; billing is ${mode.billing})`,
  };
  const target = endpointOf(env.DATABASE_URL ?? "");
  if (options.skip.includes("e2e")) Object.assign(e2e, { status: SKIPPED, reason: "left out with --skip" });
  else if (mode.clerk === "production keys") Object.assign(e2e, { status: SKIPPED, need: "development-keys", reason: "the Clerk keys are production keys (pk_live_/sk_live_): this run signs in and creates a test user, so it only uses a development instance" });
  else if (mode.clerk !== "sandbox") Object.assign(e2e, { status: SKIPPED, need: "clerk", reason: "the Clerk development keys are not in .env (NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY are missing or still the placeholders)" });
  else if (!target) Object.assign(e2e, { status: BLOCKED, need: "database", reason: "DATABASE_URL is not set to a URL: the API needs the development database" });
  else if (!isLoopback(target.host)) Object.assign(e2e, { status: BLOCKED, need: "local-database", reason: `DATABASE_URL points at ${target.host}, not at this machine: the run writes a test user, a project and a billing-customer mapping to that database. Point .env at a disposable local database, or run E2E_SANDBOX=1 pnpm test:e2e yourself if you mean it` });
  else if (!(await probe.reachable(target.host, target.port))) Object.assign(e2e, { status: BLOCKED, need: "database", reason: `the database in DATABASE_URL is not reachable (${target.host}:${target.port}): pnpm db:up, then pnpm db:migrate` });
  else {
    const executable = probe.chromiumPath();
    if (!executable || !probe.exists(executable)) Object.assign(e2e, { status: BLOCKED, need: "chromium", reason: "Chromium for Playwright is not installed: pnpm --filter web exec playwright install chromium" });
  }
  parts.push(e2e);
  return parts;
}

const countOf = (summary, word) => Number(new RegExp(`(\\d+) ${word}\\b`).exec(summary ?? "")?.[1] ?? 0);

/** The rows of the integration table that `pnpm check:setup` printed in its own evidence block. */
export function integrationRows(output) {
  const lines = output.split(/\r?\n/);
  const start = lines.findIndex((line) => /^\| Integration \| Integration mode \|/.test(line));
  if (start < 0) return [];
  const rows = [];
  for (let index = start; index < lines.length && lines[index].startsWith("|"); index += 1) rows.push(lines[index]);
  return rows;
}

/**
 * How a part that ran ended. A run that exits 0 but ran no test verified nothing, so it is SKIPPED, not PASS.
 * @returns {{status: string, detail: string}}
 */
export function judge(part, outcome, output) {
  if (outcome.missing) return { status: FAIL, detail: "the command could not be started" };
  if (part.id === "setup") {
    const rows = integrationRows(output).slice(2);
    const outcomes = rows.map((row) => row.split("|")[3]?.trim());
    const count = (word) => outcomes.filter((value) => value?.startsWith(word)).length;
    const tally = rows.length ? `${count("PASS")} PASS, ${count("FAIL")} FAIL, ${count("SKIPPED")} SKIPPED` : "see the lines above";
    return outcome.code === 0 ? { status: PASS, detail: tally } : { status: FAIL, detail: `exit ${outcome.code}; ${tally}` };
  }
  const summary = summarizeOutput(part.id === "stripe" ? "pytest" : "playwright", outcome.tail ?? []);
  if (outcome.code !== 0) return { status: FAIL, detail: `exit ${outcome.code}${summary ? `; ${summary}` : ""}` };
  if (countOf(summary, "passed") === 0) return { status: SKIPPED, detail: `it exited 0 but ran no test${summary ? ` (${summary})` : ""}: see the skip reasons above` };
  return { status: PASS, detail: summary ?? "" };
}

const statusOf = (status) => ({ PASS: "PASS", FAIL: "FAIL", BLOCKED: "BLOCKED", SKIPPED: "NOT VERIFIED" })[status];

/** The spec section 20 record: from what actually ran. SKIPPED parts are NOT VERIFIED. */
export function evidenceBlock({ results, meta, setupRows, sendTestEvents, config, appEnv }) {
  const ran = results.filter((result) => [PASS, FAIL].includes(result.status));
  const contacted = [
    ran.some((result) => result.id === "setup") && "the providers in sandbox mode (see the integration table)",
    ran.some((result) => result.id === "stripe") && "Stripe (test mode)",
    ran.some((result) => result.id === "e2e") && `Clerk (development instance)${config.billing === "sandbox" ? ", Stripe (test mode)" : ""}`,
  ].filter(Boolean);
  const tree = meta.dirty === null ? "working tree unknown" : meta.dirty ? "working tree has uncommitted changes" : "working tree clean";
  const lines = [
    `## Evidence: sandbox verification (${meta.date})`,
    "",
    "| Field | Value |",
    "|---|---|",
    "| Requirements | DEC-006 (sandbox verification), TEST-04 (integration verification) |",
    `| Date | ${meta.date} |`,
    `| Commit | ${meta.commit} (${tree}) |`,
    `| Environment | ${appEnv ? `APP_ENV=${appEnv}; ` : ""}Node ${meta.node}; the root .env of this machine |`,
    "| Command | `pnpm verify:sandbox` |",
    `| Providers contacted | ${contacted.join("; ") || "none"} |`,
    `| Test events sent | ${sendTestEvents ? "one PostHog event and one Sentry issue, for each of them in sandbox mode" : "none (run with --send-test-events to send them)"} |`,
    "| Artifact | the terminal output of this command |",
    "",
    "| Part | Command | Integration mode | Outcome | Status | Result |",
    "|---|---|---|---|---|---|",
    ...results.map((result) => {
      const ranIt = [PASS, FAIL].includes(result.status);
      return `| ${result.name} | \`${result.command}\` | ${ranIt ? result.mode : "not run"} | ${result.status} | ${statusOf(result.status)} | ${result.detail.replaceAll("|", "\\|")} |`;
    }),
  ];
  if (setupRows.length) lines.push("", "Integration diagnostics (from `pnpm check:setup`):", "", ...setupRows);
  lines.push(
    "",
    "Limitations: a `sandbox` pass proves the checks above against test credentials only (TEST-04), not production behaviour or any `live` integration. " +
      "Skipped parts were not run and stay NOT VERIFIED until they run and pass. The Stripe suite creates and deletes customers, test clocks and subscriptions in the Stripe sandbox; " +
      "the end-to-end run creates a Clerk test user in the development instance and writes a test user, a project (deleted again) and a billing-customer mapping to the DATABASE_URL database. " +
      "No secrets, tokens, or personal data are recorded here.",
  );
  return lines.join("\n");
}

/** What only the owner can do next, once for each thing that stopped a part. */
export function nextSteps({ results }) {
  const needs = new Set(results.filter((result) => result.need).map((result) => result.need));
  const steps = [];
  if (needs.has("env")) steps.push("Create .env: `pnpm init-template` (it creates one and renames the template), or `cp .env.example .env`.");
  if (needs.has("clerk") || needs.has("keys")) {
    steps.push(
      "Clerk: create a free development instance at https://dashboard.clerk.com, enable Organizations with Membership optional (Organization settings), and paste its publishable and secret keys into NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY in .env (docs/setup.md#clerk). The end-to-end run creates its own test user.",
    );
  }
  if (needs.has("development-keys")) steps.push("Clerk: .env holds production keys. Use the keys of a development instance there; keep the production ones in the deployment's environment.");
  if (needs.has("stripe") || needs.has("keys")) {
    steps.push("Stripe: create a sandbox, put its sk_test_ key in STRIPE_SECRET_KEY, run `pnpm setup:stripe --currency usd --monthly 900 --yearly 9000`, and paste the lines it prints into .env.");
  }
  if (needs.has("database")) steps.push("Database: `pnpm db:up` (needs Docker), then `pnpm db:migrate`; verify never starts it.");
  if (needs.has("local-database")) steps.push("Database: point DATABASE_URL in .env at a disposable local database (`pnpm db:up`), never at a hosted project.");
  if (needs.has("chromium")) steps.push("Browser: `pnpm --filter web exec playwright install chromium`.");
  if (results.some((result) => result.status === BLOCKED || result.need)) steps.push("Then run `pnpm verify:sandbox` again.");
  if (results.some((result) => result.status === FAIL)) steps.push("Fix the FAIL lines above (each says what failed), then run `pnpm verify:sandbox` again.");
  steps.push("To keep a result as evidence (spec section 20), paste the record above into a new file under dev-docs/evidence/. Parts that were skipped stay NOT VERIFIED.");
  return steps;
}

/**
 * The whole command. Returns the exit status: 0 unless a part failed or was blocked, 2 for a usage error.
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
    stdout = process.stdout,
  } = {},
) {
  let options;
  try {
    options = parseArguments(argv);
  } catch (problem) {
    if (!(problem instanceof UsageError)) throw problem;
    error(`verify:sandbox: ${problem.message}\n\n${USAGE}`);
    return 2;
  }
  if (options.help) {
    log(USAGE);
    return 0;
  }

  const hasDotenv = readDotenv(root) !== null;
  const effective = effectiveEnv(root, env);
  const config = configuration(effective);
  const parts = await planParts({ root, env: effective, hasDotenv, options, probe });
  log(`verify:sandbox: ${parts.filter((part) => !part.status).length} of ${parts.length} parts can run (Clerk: ${config.clerk}; billing: ${config.billing}; LLM: ${config.llm}; analytics: ${config.analytics}; error reporting: ${config.errors}).`);

  const results = [];
  let setupRows = [];
  for (const part of parts) {
    if (part.status) {
      log(`\nverify:sandbox: ${part.name}: ${part.status} - ${part.reason}`);
      results.push({ ...part, detail: part.reason, ms: 0 });
      continue;
    }
    log(`\n=== ${part.name}: ${part.command} ===`);
    const collected = [];
    const tee = { write: (chunk) => (stdout.write(chunk), collected.push(String(chunk))) };
    const start = now();
    const commandEnv = { ...env, NEXT_TELEMETRY_DISABLED: "1", ...(part.id === "e2e" ? { E2E_SANDBOX: "1" } : {}) };
    const args = part.id === "setup" ? ["check:setup", ...(options.sendTestEvents ? ["--send-test-events"] : [])] : part.id === "stripe" ? ["test:stripe-sandbox", "--require-db"] : ["test:e2e"];
    const outcome = await run({ command: "pnpm", args, cwd: root, env: commandEnv, stdout: tee });
    const verdict = judge(part, outcome, collected.join(""));
    if (part.id === "setup") setupRows = integrationRows(collected.join(""));
    results.push({ ...part, ...verdict, ms: now() - start });
    log(`verify:sandbox: ${part.name}: ${verdict.status}${verdict.detail ? ` (${verdict.detail})` : ""}`);
  }

  log("");
  log(renderTable(["part", "result", "time", "detail"], results.map((result) => [result.id, result.status, result.ms ? formatDuration(result.ms) : "-", result.detail])));
  log("");
  log(evidenceBlock({ results, meta: meta(), setupRows, sendTestEvents: options.sendTestEvents && results.some((result) => result.id === "setup" && [PASS, FAIL].includes(result.status)), config, appEnv: effective.APP_ENV?.trim() }));
  log("");
  log("Next steps:");
  nextSteps({ results }).forEach((step, index) => log(`  ${index + 1}. ${step}`));
  const bad = results.filter((result) => [FAIL, BLOCKED].includes(result.status));
  log(`\nverify:sandbox: ${bad.length ? `FAIL - ${bad.map((result) => `${result.id} ${result.status}`).join(", ")}` : results.every((result) => result.status === SKIPPED) ? "nothing was verified: every part was skipped (exit 0)" : `done - ${results.filter((result) => result.status === PASS).length} passed, ${results.filter((result) => result.status === SKIPPED).length} skipped`}.`);
  return bad.length ? 1 : 0;
}
