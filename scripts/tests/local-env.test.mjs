import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { parseEnv } from "node:util";

import { MESSAGES } from "../lib/dev-stripe.mjs";
import { prepareLocalEnv } from "../lib/local-env.mjs";

const API_KEY = `sk_${"test"}_${"k".repeat(30)}`;
const SIGNING_SECRET = `whsec_${"b2".repeat(32)}`;

function setup(t, dotenv) {
  const root = mkdtempSync(path.join(tmpdir(), "local-env-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(path.join(root, ".env"), dotenv);
  const logged = [];
  const calls = { secret: [], preflight: [] };
  const base = { PATH: "/bin", HOME: "/home/x" };
  return {
    root,
    logged,
    calls,
    base,
    options: (readSecret, preflight) => ({
      root,
      command: "dev",
      base,
      log: (line) => logged.push(line),
      readSecret: async (arguments_) => {
        calls.secret.push(arguments_);
        return readSecret(arguments_);
      },
      preflight: async (arguments_) => {
        calls.preflight.push(arguments_);
        return preflight(arguments_);
      },
    }),
  };
}
const ok = async () => ({ ok: true, problems: [], warnings: [] });

test("sandbox billing: the signing secret goes to the API's environment only, and is never logged", async (t) => {
  const s = setup(t, `BILLING_MODE=sandbox\nSTRIPE_SECRET_KEY=${API_KEY}\n`);
  const result = await prepareLocalEnv(s.options(async () => ({ status: "ok", secret: SIGNING_SECRET }), ok));
  assert.deepEqual(result, { ok: true, apiEnv: { STRIPE_WEBHOOK_SECRET: SIGNING_SECRET }, listen: { apiKey: API_KEY, secret: SIGNING_SECRET } });
  assert.equal(s.calls.secret[0].apiKey, API_KEY);
  // The preflight sees the secret (so a sandbox configuration validates), but the process environment is untouched.
  assert.equal(s.calls.preflight[0].env.STRIPE_WEBHOOK_SECRET, SIGNING_SECRET);
  assert.equal(s.calls.preflight[0].childEnv.STRIPE_WEBHOOK_SECRET, SIGNING_SECRET);
  assert.equal(s.base.STRIPE_WEBHOOK_SECRET, undefined);
  assert.equal(s.logged.join("\n").includes(SIGNING_SECRET), false);
});

test("mock billing never calls the Stripe CLI", async (t) => {
  const s = setup(t, "BILLING_MODE=mock\n");
  const result = await prepareLocalEnv(s.options(async () => assert.fail("the CLI was called"), ok));
  assert.deepEqual(result, { ok: true, apiEnv: {}, listen: null });
  assert.deepEqual(s.logged, []);
});

test("a missing or logged-out CLI gives one clear line, and the preflight (stubbed to pass here) decides whether the run goes on", async (t) => {
  for (const [status, message] of [["missing", MESSAGES.missing], ["failed", MESSAGES.failed], ["invalid", MESSAGES.invalid]]) {
    const s = setup(t, `BILLING_MODE=sandbox\nSTRIPE_SECRET_KEY=${API_KEY}\n`);
    const result = await prepareLocalEnv(s.options(async () => ({ status }), ok));
    assert.deepEqual(result, { ok: true, apiEnv: {}, listen: null });
    assert.deepEqual(s.logged, [`dev: ${message}`]);
  }
});

test("the caller decides whether the placeholder Clerk pair may start the web app", async (t) => {
  for (const requireClerk of [true, false]) {
    const s = setup(t, "BILLING_MODE=mock\n");
    await prepareLocalEnv({ ...s.options(async () => assert.fail("the CLI was called"), ok), requireClerk });
    assert.equal(s.calls.preflight[0].requireClerk, requireClerk);
  }
});

test("a failed preflight is reported as a short list and the run does not start", async (t) => {
  const s = setup(t, "BILLING_MODE=mock\n");
  const result = await prepareLocalEnv(
    s.options(ok, async () => ({ ok: false, problems: ["STRIPE_SECRET_KEY is required"], warnings: ["Clerk is not configured"] })),
  );
  assert.equal(result.ok, false);
  assert.equal(s.logged[0], "dev: Clerk is not configured");
  assert.match(s.logged[1], /^dev: the configuration has problems\.\n {2}- STRIPE_SECRET_KEY is required\nEdit \.env/);
});

// -- With the validators the API and the web app really run (no stubbed preflight) ----------------------------
// docs/setup.md#stripe says what `pnpm dev` does with BILLING_MODE=sandbox when the Stripe CLI is missing.
// That depends on the API's own rule (STRIPE_WEBHOOK_SECRET must be a whsec_ value), so the rule runs here:
// `uv run python -m app.cli check-config` and the web app's config loader, from the repository.

const repo = path.resolve(import.meta.dirname, "../..");
const toolsMissing = !existsSync(path.join(repo, "apps/api/.venv")) || spawnSync("uv", ["--version"]).status !== 0;
const SKIP_REAL = toolsMissing && "needs uv and the API's dependencies (pnpm bootstrap)";
const WEBHOOK_SECRET = `whsec_${"c3".repeat(16)}`;

/**
 * The environment of a run: .env.example (a valid configuration, and every variable it names, so the
 * developer's own .env cannot change the result) with sandbox billing, which needs its Stripe values.
 */
function sandboxEnvironment(extra = {}) {
  const example = parseEnv(readFileSync(path.join(repo, ".env.example"), "utf8"));
  return {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    ...example,
    BILLING_MODE: "sandbox",
    STRIPE_SECRET_KEY: API_KEY,
    STRIPE_PRICE_PRO_MONTHLY: "price_monthly_example",
    STRIPE_PRICE_PRO_YEARLY: "price_yearly_example",
    STRIPE_PRODUCT_PRO: "prod_example",
    STRIPE_WEBHOOK_SECRET: "",
    ...extra,
  };
}

async function realRun(cli, extra) {
  const logged = [];
  const result = await prepareLocalEnv({
    root: repo,
    command: "dev",
    base: sandboxEnvironment(extra),
    log: (line) => logged.push(line),
    readSecret: async () => cli,
  });
  return { result, logged: logged.join("\n") };
}

test("sandbox billing without the Stripe CLI and without a signing secret stops pnpm dev, on the API's own rule", { skip: SKIP_REAL }, async () => {
  const { result, logged } = await realRun({ status: "missing" });
  assert.equal(result.ok, false);
  assert.deepEqual(result.apiEnv, {});
  assert.equal(result.listen, null);
  assert.ok(logged.includes(`dev: ${MESSAGES.missing}`));
  const problems = logged.split("\n").filter((line) => line.startsWith("  - "));
  assert.deepEqual(problems, ["  - STRIPE_WEBHOOK_SECRET is required and must start with whsec_"]);
});

test("sandbox billing without the Stripe CLI goes on, without a listener, once .env holds a signing secret", { skip: SKIP_REAL }, async () => {
  const { result, logged } = await realRun({ status: "missing" }, { STRIPE_WEBHOOK_SECRET: WEBHOOK_SECRET });
  assert.deepEqual(result, { ok: true, apiEnv: {}, listen: null });
  assert.ok(logged.includes(`dev: ${MESSAGES.missing}`));
  assert.ok(!logged.includes("the configuration has problems"));
  assert.ok(!logged.includes(WEBHOOK_SECRET));
});

test("with the Stripe CLI the signing secret needs no .env at all: the API gets it for the run only", { skip: SKIP_REAL }, async () => {
  const { result, logged } = await realRun({ status: "ok", secret: WEBHOOK_SECRET });
  assert.deepEqual(result, { ok: true, apiEnv: { STRIPE_WEBHOOK_SECRET: WEBHOOK_SECRET }, listen: { apiKey: API_KEY, secret: WEBHOOK_SECRET } });
  assert.ok(!logged.includes("the configuration has problems"));
  assert.ok(!logged.includes(WEBHOOK_SECRET));
});
