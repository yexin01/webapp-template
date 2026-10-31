import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { PLACEHOLDER_PUBLISHABLE_KEY, PLACEHOLDER_SECRET_KEY } from "../lib/clerk.mjs";
import { FAIL, PASS, SKIPPED } from "../lib/steps.mjs";
import { BLOCKED, configuration, evidenceBlock, integrationRows, judge, main, nextSteps, parseArguments, planParts } from "../lib/verify-sandbox.mjs";
import { UsageError } from "../lib/verify.mjs";

// Built at run time: no secret-shaped literal in the source. The canaries must never reach the output.
const devPublishable = `pk_${"test"}_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`;
const devSecret = `sk_${"test"}_${"c".repeat(24)}CANARYCLERK`;
const stripeKey = `sk_${"test"}_${"s".repeat(24)}CANARYSTRIPE`;
const livePublishable = `pk_${"live"}_${Buffer.from("clerk.example.com$").toString("base64")}`;
const clerk = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: devPublishable, CLERK_SECRET_KEY: devSecret };
const stripe = { STRIPE_SECRET_KEY: stripeKey, STRIPE_PRICE_PRO_MONTHLY: "price_month", STRIPE_PRICE_PRO_YEARLY: "price_year", STRIPE_PRODUCT_PRO: "prod_pro" };
const database = { DATABASE_URL: "postgresql://postgres:CANARYDB@127.0.0.1:5999/app" };

const probe = (overrides = {}) => ({
  reachable: async () => true,
  chromiumPath: () => "/browsers/chromium",
  exists: () => true,
  ...overrides,
});
const noOptions = { sendTestEvents: false, skip: [] };

function scratchRoot(t, dotenv = null) {
  const root = mkdtempSync(path.join(tmpdir(), "verify-sandbox-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, "apps/api/tests"), { recursive: true });
  writeFileSync(path.join(root, "apps/api/tests/conftest.py"), 'DEFAULT_TEST_DATABASE_URL = "postgresql://user:pw@127.0.0.1:55555/app"\n');
  if (dotenv !== null) writeFileSync(path.join(root, ".env"), dotenv);
  return root;
}

const plan = (t, env, { hasDotenv = true, options = noOptions, probe: p = probe() } = {}) => planParts({ root: scratchRoot(t), env, hasDotenv, options, probe: p });
const byId = (parts) => Object.fromEntries(parts.map((part) => [part.id, part]));

test("the options are the test-event pass-through and the parts to leave out", () => {
  assert.deepEqual(parseArguments([]), { sendTestEvents: false, skip: [], help: false });
  assert.deepEqual(parseArguments(["--send-test-events", "--skip", "e2e", "--skip", "stripe"]), { sendTestEvents: true, skip: ["e2e", "stripe"], help: false });
  assert.throws(() => parseArguments(["--skip", "deploy"]), (error) => error instanceof UsageError && /unknown part "deploy" \(parts: setup, stripe, e2e\)/.test(error.message));
  assert.throws(() => parseArguments(["--force"]), UsageError);
});

test("the configuration is read from names and modes, with the API's local defaults", () => {
  assert.deepEqual(configuration({}), { clerk: "not set", billing: "mock", llm: "mock", analytics: "disabled", errors: "disabled" });
  assert.equal(configuration({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY, CLERK_SECRET_KEY: PLACEHOLDER_SECRET_KEY }).clerk, "not configured");
  assert.equal(configuration(clerk).clerk, "sandbox");
  assert.equal(configuration({ ...clerk, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable }).clerk, "production keys");
  assert.equal(configuration({ ...clerk, CLERK_SECRET_KEY: "" }).clerk, "not set");
  assert.equal(configuration({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_replace_me", CLERK_SECRET_KEY: "sk_test_replace_me" }).clerk, "not configured");
  assert.equal(configuration({ BILLING_MODE: " sandbox " }).billing, "sandbox");
});

test("with nothing configured every part is skipped, and each says what is missing", async (t) => {
  const parts = byId(await plan(t, {}, { hasDotenv: false }));
  assert.deepEqual(Object.values(parts).map((part) => part.status), [SKIPPED, SKIPPED, SKIPPED]);
  assert.match(parts.setup.reason, /there is no \.env file yet/);
  assert.match(parts.stripe.reason, /^STRIPE_SECRET_KEY is not set/);
  assert.match(parts.e2e.reason, /Clerk development keys are not in \.env/);
});

test("a .env that still holds the placeholders and mocks is nothing configured", async (t) => {
  const placeholders = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY, CLERK_SECRET_KEY: PLACEHOLDER_SECRET_KEY, BILLING_MODE: "mock", LLM_MODE: "mock", ANALYTICS_MODE: "disabled", ERROR_REPORTING_MODE: "disabled" };
  const parts = byId(await plan(t, placeholders));
  assert.match(parts.setup.reason, /^nothing is in sandbox mode yet/);
  assert.equal(parts.e2e.status, SKIPPED);
});

test("the setup check runs once anything is in sandbox mode: Clerk keys, or a sandbox mode", async (t) => {
  assert.equal(byId(await plan(t, clerk)).setup.status, undefined);
  for (const mode of ["BILLING_MODE", "LLM_MODE", "ANALYTICS_MODE", "ERROR_REPORTING_MODE"]) {
    assert.equal(byId(await plan(t, { [mode]: "sandbox" })).setup.status, undefined, mode);
  }
});

test("the Stripe suite needs an unrestricted sandbox key and the Pro prices and product, and can be turned off", async (t) => {
  const reason = async (env) => byId(await plan(t, env)).stripe.reason;
  assert.equal(byId(await plan(t, stripe)).stripe.status, undefined);
  assert.match(await reason({ ...stripe, STRIPE_SECRET_KEY: `rk_${"test"}_${"r".repeat(24)}` }), /restricted key.*unrestricted sandbox secret key/);
  assert.match(await reason({ ...stripe, STRIPE_SECRET_KEY: `sk_${"live"}_${"l".repeat(24)}` }), /not a sandbox secret key/);
  for (const missing of [{ STRIPE_PRICE_PRO_YEARLY: "" }, { STRIPE_PRODUCT_PRO: "" }, { STRIPE_PRODUCT_PRO: "pro" }]) {
    assert.match(await reason({ ...stripe, ...missing }), /STRIPE_PRICE_PRO_MONTHLY, STRIPE_PRICE_PRO_YEARLY and STRIPE_PRODUCT_PRO are not all set \(pnpm setup:stripe/);
  }
  assert.equal(await reason({ ...stripe, STRIPE_SANDBOX_TESTS: "0" }), "STRIPE_SANDBOX_TESTS=0 leaves it out");
  assert.equal(await reason({ ...stripe, STRIPE_SANDBOX_TESTS: "false" }), "STRIPE_SANDBOX_TESTS=0 leaves it out");
  assert.equal(byId(await plan(t, { ...stripe, STRIPE_SANDBOX_TESTS: "1" })).stripe.status, undefined);
  assert.ok(!(await reason({ ...stripe, STRIPE_SECRET_KEY: `rk_${"test"}_${"r".repeat(24)}` })).includes("rrrr"), "the key is never in a reason");
});

test("a Stripe suite whose test database is down is BLOCKED, with the command to start it", async (t) => {
  const stripePart = byId(await plan(t, stripe, { probe: probe({ reachable: async () => false }) })).stripe;
  assert.equal(stripePart.status, BLOCKED);
  assert.match(stripePart.reason, /test database is not reachable \(127\.0\.0\.1:55555\): pnpm db:up/);
  assert.ok(!stripePart.reason.includes("pw"));
});

test("the end-to-end run needs Clerk development keys, a local database that answers, and Chromium", async (t) => {
  const run = async (env, options) => byId(await plan(t, { ...clerk, ...database, ...env }, options)).e2e;
  assert.equal((await run({})).status, undefined);
  assert.equal((await run({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable })).status, SKIPPED);
  assert.match((await run({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable })).reason, /production keys/);
  assert.match((await run({ CLERK_SECRET_KEY: "" })).reason, /Clerk development keys are not in \.env/);
  assert.equal((await run({ DATABASE_URL: "" })).status, BLOCKED);
  const remote = await run({ DATABASE_URL: "postgresql://app_login:CANARYDB@db.abcdefgh.supabase.co:5432/postgres" });
  assert.equal(remote.status, BLOCKED);
  assert.match(remote.reason, /points at db\.abcdefgh\.supabase\.co, not at this machine/);
  assert.ok(!remote.reason.includes("CANARYDB"));
  const down = await run({}, { probe: probe({ reachable: async () => false }) });
  assert.equal(down.status, BLOCKED);
  assert.match(down.reason, /127\.0\.0\.1:5999\): pnpm db:up, then pnpm db:migrate/);
  const noBrowser = await run({}, { probe: probe({ chromiumPath: () => null }) });
  assert.equal(noBrowser.status, BLOCKED);
  assert.match(noBrowser.reason, /pnpm --filter web exec playwright install chromium/);
});

test("--skip leaves a part out whatever the .env holds", async (t) => {
  const parts = byId(await plan(t, { ...clerk, ...stripe, ...database }, { options: { sendTestEvents: false, skip: ["setup", "stripe", "e2e"] } }));
  assert.deepEqual(Object.values(parts).map((part) => part.reason), ["left out with --skip", "left out with --skip", "left out with --skip"]);
});

// What `pnpm check:setup` prints for its record: the rows are carried into this one.
const setupOutput = [
  "Database       PASS",
  "## Evidence: integration diagnostics (2026-10-30)",
  "",
  "| Field | Value |",
  "|---|---|",
  "| Commit | abc |",
  "",
  "| Integration | Integration mode | Outcome | Status | Result |",
  "|---|---|---|---|---|",
  "| PostgreSQL | local | PASSED | PASS | Connected as app_api. |",
  "| Clerk | sandbox | PASSED | PASS | Secret key accepted. |",
  "| Stripe | mocked | SKIPPED | NOT VERIFIED | Mock mode. |",
  "",
  "Limitations: ...",
].join("\n");

test("the integration rows of check:setup's own record are found, and only those", () => {
  const rows = integrationRows(setupOutput);
  assert.equal(rows.length, 5);
  assert.match(rows[0], /^\| Integration \| Integration mode \|/);
  assert.deepEqual(integrationRows("no table here"), []);
});

test("a part's verdict: setup by exit status, the suites by whether they ran a test", () => {
  const part = (id) => ({ id });
  assert.deepEqual(judge(part("setup"), { code: 0 }, setupOutput), { status: PASS, detail: "2 PASS, 0 FAIL, 1 SKIPPED" });
  assert.deepEqual(judge(part("setup"), { code: 1 }, setupOutput), { status: FAIL, detail: "exit 1; 2 PASS, 0 FAIL, 1 SKIPPED" });
  assert.deepEqual(judge(part("setup"), { code: 1 }, "preflight output only"), { status: FAIL, detail: "exit 1; see the lines above" });
  assert.deepEqual(judge(part("stripe"), { code: 0, tail: ["==== 5 passed in 120.5s (0:02:00) ===="] }, ""), { status: PASS, detail: "5 passed in 120.5s (0:02:00)" });
  assert.equal(judge(part("stripe"), { code: 0, tail: ["==== 5 skipped in 0.2s ===="] }, "").status, SKIPPED);
  assert.match(judge(part("stripe"), { code: 0, tail: ["==== 5 skipped in 0.2s ===="] }, "").detail, /exited 0 but ran no test \(5 skipped in 0\.2s\)/);
  assert.deepEqual(judge(part("stripe"), { code: 1, tail: ["==== 1 failed, 4 passed in 9.0s ===="] }, ""), { status: FAIL, detail: "exit 1; 1 failed, 4 passed in 9.0s" });
  assert.deepEqual(judge(part("e2e"), { code: 0, tail: ["  14 skipped", "  56 passed (2.1m)"] }, ""), { status: PASS, detail: "14 skipped, 56 passed" });
  assert.equal(judge(part("e2e"), { code: 0, tail: ["  70 skipped"] }, "").status, SKIPPED);
  assert.equal(judge(part("e2e"), { code: null, missing: true }, "").status, FAIL);
});

const meta = { commit: "abc123def456", dirty: false, node: "24.1.0", date: "2026-10-30" };
const ran = (id, name, command, mode, status, detail) => ({ id, name, command, mode, status, detail, ms: 1000 });

test("the record states date, commit, environment, mode, outcome and status per part, and only from what ran", () => {
  const block = evidenceBlock({
    results: [
      ran("setup", "Setup check", "pnpm check:setup", "per integration", PASS, "2 PASS"),
      ran("stripe", "Stripe sandbox suite", "pnpm test:stripe-sandbox --require-db", "sandbox (Stripe test mode)", SKIPPED, "STRIPE_SECRET_KEY is not set"),
      ran("e2e", "Sandbox end-to-end", "E2E_SANDBOX=1 pnpm test:e2e", "sandbox (Clerk development instance; billing is mock)", FAIL, "exit 1; 1 failed | 4 passed"),
    ],
    meta,
    setupRows: integrationRows(setupOutput),
    sendTestEvents: false,
    config: { billing: "mock" },
    appEnv: "local",
  });
  assert.match(block, /^## Evidence: sandbox verification \(2026-10-30\)/);
  assert.match(block, /\| Requirements \| DEC-006 \(sandbox verification\), TEST-04/);
  assert.match(block, /\| Commit \| abc123def456 \(working tree clean\) \|/);
  assert.match(block, /\| Environment \| APP_ENV=local; Node 24\.1\.0;/);
  assert.match(block, /\| Providers contacted \| the providers in sandbox mode \(see the integration table\); Clerk \(development instance\) \|/);
  assert.match(block, /\| Setup check \| `pnpm check:setup` \| per integration \| PASS \| PASS \| 2 PASS \|/);
  assert.match(block, /\| Stripe sandbox suite \| `pnpm test:stripe-sandbox --require-db` \| not run \| SKIPPED \| NOT VERIFIED \| STRIPE_SECRET_KEY is not set \|/);
  assert.match(block, /\| Sandbox end-to-end \| `E2E_SANDBOX=1 pnpm test:e2e` \| sandbox \(Clerk development instance; billing is mock\) \| FAIL \| FAIL \| exit 1; 1 failed \\\| 4 passed \|/);
  assert.match(block, /Integration diagnostics \(from `pnpm check:setup`\):\n\n\| Integration \| Integration mode/);
  assert.match(block, /Test events sent \| none \(run with --send-test-events/);
  assert.match(block, /No secrets, tokens, or personal data are recorded here\./);
});

test("each thing that stopped a part is asked for once, in the order of the setup", () => {
  const steps = nextSteps({
    results: [
      { status: SKIPPED, need: "env" },
      { status: SKIPPED, need: "stripe" },
      { status: SKIPPED, need: "clerk" },
      { status: BLOCKED, need: "database" },
      { status: BLOCKED, need: "database" },
      { status: BLOCKED, need: "chromium" },
    ],
  });
  assert.equal(steps.length, 7);
  assert.match(steps[0], /^Create \.env/);
  assert.match(steps[1], /^Clerk: .*Membership optional/);
  assert.match(steps[2], /^Stripe: .*pnpm setup:stripe/);
  assert.match(steps[3], /^Database: `pnpm db:up`/);
  assert.match(steps[4], /^Browser: .*playwright install chromium/);
  assert.match(steps[5], /^Then run `pnpm verify:sandbox` again\./);
  assert.match(steps[6], /dev-docs\/evidence\//);
  assert.ok(nextSteps({ results: [{ status: FAIL }] }).some((step) => /^Fix the FAIL lines/.test(step)));
});

const sink = () => {
  const lines = [];
  return { lines, fn: (line) => void lines.push(String(line)) };
};

/** Runs main with a fake `run` that answers per script. */
async function runMain(t, argv, { env = {}, dotenv = "", answers = {}, probeOverrides = {} } = {}) {
  const root = scratchRoot(t, dotenv);
  const out = sink();
  const err = sink();
  const calls = [];
  let time = 0;
  const status = await main(argv, {
    root,
    env,
    probe: probe(probeOverrides),
    meta: () => meta,
    now: () => (time += 1000),
    log: out.fn,
    error: err.fn,
    stdout: { write: (chunk) => out.lines.push(String(chunk)) },
    run: async (call) => {
      calls.push(call);
      const answer = answers[call.args[0]] ?? { code: 0, tail: [], output: "" };
      if (answer.output) call.stdout.write(answer.output);
      return answer;
    },
  });
  return { status, out: out.lines.join("\n"), err: err.lines.join("\n"), calls };
}

const everything = { ...clerk, ...stripe, ...database, BILLING_MODE: "sandbox", APP_ENV: "local" };
const passing = {
  "check:setup": { code: 0, tail: [], output: setupOutput },
  "test:stripe-sandbox": { code: 0, tail: ["==== 5 passed in 120.5s ===="] },
  "test:e2e": { code: 0, tail: ["  14 skipped", "  56 passed (2.1m)"] },
};

test("with nothing configured nothing runs, the exit status is 0, and the record says NOT VERIFIED", async (t) => {
  const { status, out, calls } = await runMain(t, [], { dotenv: null });
  assert.equal(status, 0);
  assert.equal(calls.length, 0);
  assert.match(out, /verify:sandbox: 0 of 3 parts can run/);
  assert.equal((out.match(/\| NOT VERIFIED \|/g) ?? []).length, 3);
  assert.match(out, /Next steps:\n  1\. Create \.env/);
  assert.match(out, /verify:sandbox: nothing was verified: every part was skipped \(exit 0\)\./);
});

test("everything configured runs the three parts, the end-to-end one alone with the sandbox switch", async (t) => {
  const { status, out, calls } = await runMain(t, [], { env: everything, answers: passing });
  assert.equal(status, 0);
  assert.deepEqual(calls.map((call) => [call.command, ...call.args]), [["pnpm", "check:setup"], ["pnpm", "test:stripe-sandbox", "--require-db"], ["pnpm", "test:e2e"]]);
  assert.deepEqual(calls.map((call) => call.env.E2E_SANDBOX), [undefined, undefined, "1"]);
  assert.ok(calls.every((call) => call.cwd.includes("verify-sandbox-")));
  assert.match(out, /verify:sandbox: done - 3 passed, 0 skipped\./);
  assert.match(out, /\| Sandbox end-to-end \| `E2E_SANDBOX=1 pnpm test:e2e` \| sandbox \(Clerk development instance; billing is sandbox\) \| PASS \| PASS \| 14 skipped, 56 passed \|/);
  assert.match(out, /Providers contacted \| the providers in sandbox mode \(see the integration table\); Stripe \(test mode\); Clerk \(development instance\), Stripe \(test mode\)/);
  assert.match(out, /\| PostgreSQL \| local \| PASSED \| PASS \|/);
});

test("--send-test-events reaches check:setup and is recorded", async (t) => {
  const { calls, out } = await runMain(t, ["--send-test-events"], { env: everything, answers: passing });
  assert.deepEqual(calls[0].args, ["check:setup", "--send-test-events"]);
  assert.match(out, /pnpm check:setup --send-test-events/);
  assert.match(out, /Test events sent \| one PostHog event and one Sentry issue/);
});

test("a failing part does not stop the others, and the exit status is 1", async (t) => {
  const { status, out, calls } = await runMain(t, [], { env: everything, answers: { ...passing, "test:stripe-sandbox": { code: 1, tail: ["==== 1 failed, 4 passed in 9.0s ===="] } } });
  assert.equal(status, 1);
  assert.equal(calls.length, 3);
  assert.match(out, /verify:sandbox: FAIL - stripe FAIL\./);
  assert.match(out, /Fix the FAIL lines above/);
});

test("a part that is blocked fails the run with what to do", async (t) => {
  const { status, out, calls } = await runMain(t, [], { env: everything, answers: passing, probeOverrides: { chromiumPath: () => null } });
  assert.equal(status, 1);
  assert.deepEqual(calls.map((call) => call.args[0]), ["check:setup", "test:stripe-sandbox"]);
  assert.match(out, /verify:sandbox: FAIL - e2e BLOCKED\./);
  assert.match(out, /Browser: `pnpm --filter web exec playwright install chromium`\./);
});

test("a suite that exits 0 but ran no test is not a pass", async (t) => {
  const { status, out } = await runMain(t, [], { env: everything, answers: { ...passing, "test:e2e": { code: 0, tail: ["  70 skipped"] } } });
  assert.equal(status, 0);
  assert.match(out, /\| Sandbox end-to-end \|.*\| SKIPPED \| NOT VERIFIED \| it exited 0 but ran no test/);
});

test("no value from the .env or the environment ever reaches the output", async (t) => {
  const canaries = ["CANARYCLERK", "CANARYSTRIPE", "CANARYDB", "CANARYLIVE", "CANARYRESTRICTED", devPublishable];
  const wrongStripeKey = (key) => ({ ...everything, STRIPE_SECRET_KEY: key });
  const runs = [
    await runMain(t, [], { env: everything, answers: passing }),
    await runMain(t, [], { env: everything, answers: { ...passing, "test:e2e": { code: 1, tail: ["  1 failed"] } }, probeOverrides: { reachable: async () => false } }),
    await runMain(t, [], { env: { ...clerk, ...stripe, DATABASE_URL: "postgresql://u:CANARYDB@db.example.com:5432/x" } }),
    await runMain(t, [], { env: wrongStripeKey(`sk_${"live"}_${"l".repeat(24)}CANARYLIVE`), answers: passing }),
    await runMain(t, [], { env: wrongStripeKey(`rk_${"test"}_${"r".repeat(24)}CANARYRESTRICTED`), answers: passing }),
  ];
  for (const run of runs) for (const canary of canaries) assert.ok(!(run.out + run.err).includes(canary), `${canary} reached the output`);
});

test("--skip, --help and a usage error", async (t) => {
  const skipped = await runMain(t, ["--skip", "e2e", "--skip", "setup"], { env: everything, answers: passing });
  assert.deepEqual(skipped.calls.map((call) => call.args[0]), ["test:stripe-sandbox"]);
  assert.equal((await runMain(t, ["--help"])).status, 0);
  const bad = await runMain(t, ["--skip", "deploy"]);
  assert.equal(bad.status, 2);
  assert.match(bad.err, /unknown part "deploy"/);
});
