import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

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

test("a missing or logged-out CLI gives one clear line and the run goes on without a listener", async (t) => {
  for (const [status, message] of [["missing", MESSAGES.missing], ["failed", MESSAGES.failed], ["invalid", MESSAGES.invalid]]) {
    const s = setup(t, `BILLING_MODE=sandbox\nSTRIPE_SECRET_KEY=${API_KEY}\n`);
    const result = await prepareLocalEnv(s.options(async () => ({ status }), ok));
    assert.deepEqual(result, { ok: true, apiEnv: {}, listen: null });
    assert.deepEqual(s.logged, [`dev: ${message}`]);
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
