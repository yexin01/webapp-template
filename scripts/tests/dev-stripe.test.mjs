import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { MESSAGES, listenPlan, readSigningSecret, redact, startListener } from "../lib/dev-stripe.mjs";

// Synthetic values, assembled at run time so a secret scanner has nothing to flag.
const API_KEY = `sk_${"test"}_${"k".repeat(30)}`;
const SIGNING_SECRET = `whsec_${"a1".repeat(32)}`;

test("the listener runs for BILLING_MODE=sandbox with an sk_test_ key, and for nothing else", () => {
  assert.deepEqual(listenPlan({ BILLING_MODE: "sandbox", STRIPE_SECRET_KEY: API_KEY }), { run: true, apiKey: API_KEY });
  assert.deepEqual(listenPlan({ BILLING_MODE: " Sandbox ", STRIPE_SECRET_KEY: ` ${API_KEY} ` }), { run: true, apiKey: API_KEY });
  for (const env of [{}, { BILLING_MODE: "mock", STRIPE_SECRET_KEY: API_KEY }, { BILLING_MODE: "live", STRIPE_SECRET_KEY: API_KEY }]) {
    assert.deepEqual(listenPlan(env), { run: false });
  }
});

test("a restricted or live key never reaches the CLI, and the restricted case says why", () => {
  const restricted = listenPlan({ BILLING_MODE: "sandbox", STRIPE_SECRET_KEY: `rk_${"test"}_${"r".repeat(30)}` });
  assert.equal(restricted.run, false);
  assert.equal(restricted.notice, MESSAGES.restrictedKey);
  assert.equal(listenPlan({ BILLING_MODE: "sandbox", STRIPE_SECRET_KEY: `sk_${"live"}_${"l".repeat(30)}` }).run, false);
  // A missing key is a configuration error the preflight reports.
  assert.deepEqual(listenPlan({ BILLING_MODE: "sandbox" }), { run: false });
});

test("redact masks signing secrets and keys, and any known value", () => {
  const line = `Ready! Your webhook signing secret is ${SIGNING_SECRET} (^C to quit) using ${API_KEY}; token abc123`;
  assert.equal(redact(line), "Ready! Your webhook signing secret is whsec_... (^C to quit) using sk_...; token abc123");
  assert.equal(redact("token abc123", ["abc123"]), "token ...");
});

test("the signing secret is read with the key in STRIPE_API_KEY and never in the arguments", async () => {
  const seen = [];
  const result = await readSigningSecret({
    apiKey: API_KEY,
    base: { PATH: "/bin" },
    run: async (args, env) => {
      seen.push({ args, env });
      return { status: "ok", stdout: `${SIGNING_SECRET}\n` };
    },
  });
  assert.deepEqual(result, { status: "ok", secret: SIGNING_SECRET });
  assert.deepEqual(seen[0].args, ["listen", "--print-secret"]);
  assert.equal(seen[0].env.STRIPE_API_KEY, API_KEY);
  assert.equal(seen[0].args.join(" ").includes(API_KEY), false);
});

test("output that is not a signing secret is refused, and failures keep their kind", async () => {
  const outcome = (run) => readSigningSecret({ apiKey: API_KEY, run });
  assert.deepEqual(await outcome(async () => ({ status: "ok", stdout: "A new version is available\n" })), { status: "invalid" });
  assert.deepEqual(await outcome(async () => ({ status: "ok", stdout: "whsec_short\n" })), { status: "invalid" });
  assert.deepEqual(await outcome(async () => ({ status: "missing" })), { status: "missing" });
  assert.deepEqual(await outcome(async () => ({ status: "failed" })), { status: "failed" });
  assert.deepEqual(await outcome(async () => ({ status: "timeout" })), { status: "timeout" });
});

/** A stand-in `stripe` on PATH: records its arguments and key, and behaves as FAKE_STRIPE says. */
function fakeStripe() {
  const dir = mkdtempSync(path.join(tmpdir(), "fake-stripe-"));
  const log = path.join(dir, "calls.log");
  const script = `#!/bin/sh
echo "args: $*" >> "${log}"
echo "key-in-env: $([ "$STRIPE_API_KEY" = "${API_KEY}" ] && echo yes || echo no)" >> "${log}"
case "$FAKE_STRIPE" in
  fail) echo "not logged in" >&2; exit 1 ;;
  print) echo "${SIGNING_SECRET}"; exit 0 ;;
  listen)
    echo "Ready! You are using Stripe API Version [2025-01-01]. Your webhook signing secret is ${SIGNING_SECRET} (^C to quit)"
    echo "2026-01-01 12:00:00   --> customer.subscription.updated [evt_123]" >&2
    exit 3 ;;
esac
`;
  writeFileSync(path.join(dir, "stripe"), script);
  chmodSync(path.join(dir, "stripe"), 0o755);
  return { dir, log, base: (mode) => ({ PATH: `${dir}:/usr/bin:/bin`, FAKE_STRIPE: mode }) };
}

test("with a stripe executable on PATH: the secret is read, the key stays out of the arguments", async (t) => {
  const fake = fakeStripe();
  t.after(() => rmSync(fake.dir, { recursive: true, force: true }));
  assert.deepEqual(await readSigningSecret({ apiKey: API_KEY, base: fake.base("print") }), {
    status: "ok",
    secret: SIGNING_SECRET,
  });
  const calls = readFileSync(fake.log, "utf8");
  assert.match(calls, /^args: listen --print-secret$/m);
  assert.match(calls, /^key-in-env: yes$/m);
  assert.equal(calls.includes(API_KEY), false);
  assert.deepEqual(await readSigningSecret({ apiKey: API_KEY, base: fake.base("fail") }), { status: "failed" });
});

test("without the Stripe CLI the result is 'missing'", async (t) => {
  const empty = mkdtempSync(path.join(tmpdir(), "no-stripe-"));
  t.after(() => rmSync(empty, { recursive: true, force: true }));
  assert.deepEqual(await readSigningSecret({ apiKey: API_KEY, base: { PATH: empty } }), { status: "missing" });
});

test("the listener forwards to the API, masks the secret and the key in its output, and reports its exit", async (t) => {
  const fake = fakeStripe();
  t.after(() => rmSync(fake.dir, { recursive: true, force: true }));
  const lines = [];
  const exit = await new Promise((resolve) => {
    startListener({
      apiKey: API_KEY,
      secret: SIGNING_SECRET,
      base: fake.base("listen"),
      onLine: (line) => lines.push(line),
      onExit: resolve,
    });
  });
  assert.equal(exit.code, 3);
  assert.equal(lines.some((line) => line.includes("whsec_...")), true);
  assert.equal(lines.some((line) => line.includes("customer.subscription.updated")), true);
  const output = lines.join("\n");
  assert.equal(output.includes(SIGNING_SECRET), false);
  assert.equal(output.includes(API_KEY), false);
  const calls = readFileSync(fake.log, "utf8");
  assert.match(calls, /^args: listen --forward-to http:\/\/127\.0\.0\.1:8000\/api\/v1\/webhooks\/stripe$/m);
  assert.match(calls, /^key-in-env: yes$/m);
  assert.equal(calls.includes(API_KEY), false);
});
