import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { parseEnv } from "node:util";

import { PLACEHOLDER_PUBLISHABLE_KEY, PLACEHOLDER_SECRET_KEY, clerkKeyState } from "../lib/clerk.mjs";
import { effectiveEnv, readDotenv } from "../lib/env.mjs";

const repo = path.resolve(import.meta.dirname, "../..");
const example = parseEnv(readFileSync(path.join(repo, ".env.example"), "utf8"));
const realPublishable = `pk_test_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`;

test(".env.example ships exactly the placeholder pair the scripts recognise", () => {
  assert.equal(example.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, PLACEHOLDER_PUBLISHABLE_KEY);
  assert.equal(example.CLERK_SECRET_KEY, PLACEHOLDER_SECRET_KEY);
  assert.equal(clerkKeyState(example), "placeholder");
});

test("the placeholder publishable key is well formed and names a host nobody can register", () => {
  const encoded = PLACEHOLDER_PUBLISHABLE_KEY.replace(/^pk_test_/, "");
  assert.equal(Buffer.from(encoded, "base64").toString().replace(/\$$/, "").endsWith(".invalid"), true);
  assert.equal(encoded.includes("="), false);
});

test("clerkKeyState tells the shipped pair, the older pair, real keys, and nothing apart", () => {
  const state = (publishable, secret) =>
    clerkKeyState({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable, CLERK_SECRET_KEY: secret });
  assert.equal(state(undefined, undefined), "missing");
  assert.equal(state("", ""), "missing");
  assert.equal(state("pk_test_replace_me", "sk_test_replace_me"), "legacy-placeholder");
  assert.equal(state(realPublishable, "sk_test_replace_me"), "legacy-placeholder");
  assert.equal(state(PLACEHOLDER_PUBLISHABLE_KEY, PLACEHOLDER_SECRET_KEY), "placeholder");
  // Either key of the pair is enough: one real key cannot make Clerk work.
  assert.equal(state(realPublishable, PLACEHOLDER_SECRET_KEY), "placeholder");
  assert.equal(state(PLACEHOLDER_PUBLISHABLE_KEY, "sk_test_abcdefghijklmnop"), "placeholder");
  assert.equal(state(realPublishable, "sk_test_abcdefghijklmnop"), "configured");
});

test("the process environment wins over .env, as it does for the API", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "env-test-"));
  try {
    assert.equal(readDotenv(dir), null);
    writeFileSync(path.join(dir, ".env"), "BILLING_MODE=mock\nSTRIPE_SECRET_KEY=from-file\n");
    assert.deepEqual(readDotenv(dir), { BILLING_MODE: "mock", STRIPE_SECRET_KEY: "from-file" });
    assert.deepEqual(effectiveEnv(dir, { BILLING_MODE: "sandbox", OTHER: "x", UNSET: undefined }), {
      BILLING_MODE: "sandbox",
      STRIPE_SECRET_KEY: "from-file",
      OTHER: "x",
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
