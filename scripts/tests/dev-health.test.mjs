import assert from "node:assert/strict";
import test from "node:test";

import { lastErrorLine, waitForHealth } from "../lib/dev-health.mjs";

/** A clock that advances only when the test sleeps. */
function clock() {
  let time = 0;
  return { now: () => time, sleep: async (ms) => void (time += ms) };
}
const response = (ok) => ({ ok });

test("waitForHealth returns as soon as the API answers", async () => {
  let calls = 0;
  const result = await waitForHealth({
    ...clock(),
    fetchImpl: async () => {
      calls += 1;
      if (calls < 3) throw new Error("ECONNREFUSED");
      return response(true);
    },
  });
  assert.deepEqual(result, { ok: true });
  assert.equal(calls, 3);
});

test("an API that never listens (a reloader whose worker died) is reported after the timeout", async () => {
  const slow = [];
  const result = await waitForHealth({
    ...clock(),
    timeoutMs: 20_000,
    slowAfterMs: 10_000,
    onSlow: () => slow.push(true),
    fetchImpl: async () => {
      throw new Error("ETIMEDOUT");
    },
  });
  assert.deepEqual(result, { ok: false, reason: "timeout" });
  assert.equal(slow.length, 1);
});

test("an answer that is not a success is not healthy", async () => {
  const result = await waitForHealth({ ...clock(), timeoutMs: 2_000, fetchImpl: async () => response(false) });
  assert.deepEqual(result, { ok: false, reason: "timeout" });
});

test("each request is bounded, so a socket that accepts and never answers cannot stall the wait", async () => {
  let signal;
  await waitForHealth({
    ...clock(),
    timeoutMs: 1,
    requestTimeoutMs: 1234,
    fetchImpl: async (_url, options) => {
      signal = options.signal;
      return response(false);
    },
  });
  assert.ok(signal instanceof AbortSignal);
});

test("waitForHealth stops waiting when the servers are being stopped", async () => {
  let stopped = false;
  const result = await waitForHealth({
    ...clock(),
    isStopped: () => stopped,
    fetchImpl: async () => {
      stopped = true;
      throw new Error("ECONNREFUSED");
    },
  });
  assert.deepEqual(result, { ok: false, reason: "stopped" });
});

const TRACEBACK = `INFO:     Will watch for changes in these directories: ['/work/apps/api']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
Process SpawnProcess-1:
Traceback (most recent call last):
  File "/work/apps/api/app/settings.py", line 99, in load_settings
    raise ConfigError("Invalid configuration: " + " | ".join(details)) from None
app.kernel.infrastructure.config.ConfigError: Invalid configuration: STRIPE_SECRET_KEY is required when BILLING_MODE is sandbox or live
`;

test("lastErrorLine finds the exception line of a traceback and of uvicorn's own errors", () => {
  assert.equal(
    lastErrorLine(TRACEBACK),
    "app.kernel.infrastructure.config.ConfigError: Invalid configuration: STRIPE_SECRET_KEY is required when BILLING_MODE is sandbox or live",
  );
  assert.equal(
    lastErrorLine("ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8000): address already in use\n"),
    "ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8000): address already in use",
  );
  assert.equal(lastErrorLine("ModuleNotFoundError: No module named 'psycopg'\n"), "ModuleNotFoundError: No module named 'psycopg'");
  assert.equal(lastErrorLine("INFO:     Started server process [1]\n"), null);
  assert.equal(lastErrorLine(""), null);
  assert.ok(lastErrorLine(`ConfigError: ${"x".repeat(1000)}`).length <= 300);
});
