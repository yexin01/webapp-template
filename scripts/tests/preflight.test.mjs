import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { PLACEHOLDER_PUBLISHABLE_KEY, PLACEHOLDER_SECRET_KEY } from "../lib/clerk.mjs";
import { apiProblems, parseProblems, runPreflight, webProblems } from "../lib/preflight.mjs";

const repo = path.resolve(import.meta.dirname, "../..");
const realPublishable = `pk_test_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`;
const GOOD = { code: 0, stdout: "WEBCONFIG:{\"ok\":true}\n", stderr: "", missing: false };

test("the API's problems are read from check-config's output", () => {
  const stderr = "The configuration is not valid:\n  - A_VAR is required\n  - B_VAR is malformed\nEdit .env (see .env.example), then run this again.\n";
  assert.deepEqual(parseProblems(stderr), ["A_VAR is required", "B_VAR is malformed"]);
  assert.deepEqual(apiProblems({ code: 1, stderr, missing: false }), ["A_VAR is required", "B_VAR is malformed"]);
  assert.deepEqual(apiProblems({ code: 0, stderr: "", missing: false }), []);
});

test("a check that could not run is reported as that, not as a configuration problem", () => {
  assert.match(apiProblems({ code: null, stderr: "", missing: true })[0], /uv is not installed/);
  const crashed = apiProblems({ code: 1, stderr: "Traceback...\nModuleNotFoundError: No module named 'pydantic'\n", missing: false });
  assert.match(crashed[0], /could not be checked \(ModuleNotFoundError: No module named 'pydantic'\); run pnpm bootstrap/);
});

test("the web problems come from the WEBCONFIG line, whatever else the loader printed", () => {
  const bad = { code: 0, stderr: "", stdout: 'warn something\nWEBCONFIG:{"ok":false,"problems":["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed"]}\n' };
  assert.deepEqual(webProblems(bad), ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed"]);
  assert.deepEqual(webProblems({ code: 0, stdout: 'WEBCONFIG:{"ok":null,"skipped":"x"}\n' }), []);
  assert.deepEqual(webProblems(GOOD), []);
  assert.match(webProblems({ code: 1, stdout: "" })[0], /could not be checked/);
});

function withRoot(t, dotenv) {
  const root = mkdtempSync(path.join(tmpdir(), "preflight-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  if (dotenv !== null) writeFileSync(path.join(root, ".env"), dotenv);
  return root;
}
const passing = async (command) => (command === "uv" ? { code: 0, stdout: "", stderr: "", missing: false } : GOOD);
const validatorsReject = async (command) =>
  command === "uv"
    ? { code: 1, stdout: "", stderr: "  - NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed\n  - OTHER_VAR is required\n", missing: false }
    : { code: 0, stderr: "", stdout: 'WEBCONFIG:{"ok":false,"problems":["CLERK_SECRET_KEY is malformed or still the .env.example placeholder"]}\n' };

test("without a .env the first message says how to get one", async (t) => {
  const result = await runPreflight({ root: withRoot(t, null), env: {}, childEnv: {}, run: passing });
  assert.equal(result.ok, false);
  assert.match(result.problems[0], /no \.env file: run `pnpm init-template`/);
});

test("the shipped placeholder pair passes with one warning when nothing needs a page to load (check:setup)", async (t) => {
  const env = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY, CLERK_SECRET_KEY: PLACEHOLDER_SECRET_KEY };
  const result = await runPreflight({ root: withRoot(t, ""), env, childEnv: env, run: passing });
  assert.equal(result.ok, true);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /Clerk is not configured .*sign-in is unavailable.*docs\/integrations\.md#clerk/);
});

test("starting the web app with the placeholder pair is one problem that says to paste Clerk keys (pnpm dev)", async (t) => {
  const env = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY, CLERK_SECRET_KEY: PLACEHOLDER_SECRET_KEY };
  const result = await runPreflight({ root: withRoot(t, ""), env, childEnv: env, run: passing, requireClerk: true });
  assert.equal(result.ok, false);
  assert.deepEqual(result.warnings, []);
  assert.equal(result.problems.length, 1);
  assert.match(result.problems[0], /cannot load a page without a Clerk instance.*paste its two keys into \.env/);
});

test("the older replace_me pair is a problem that says what to paste", async (t) => {
  const env = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_replace_me", CLERK_SECRET_KEY: "sk_test_replace_me" };
  const result = await runPreflight({ root: withRoot(t, ""), env, childEnv: env, run: validatorsReject });
  assert.equal(result.ok, false);
  assert.equal(result.problems.length, 2);
  assert.match(result.problems[0], /old `replace_me` placeholders: paste your Clerk development keys/);
  assert.equal(result.problems[1], "OTHER_VAR is required");
});

test("keys that are not set at all are a problem that says they are missing, not that they hold the old placeholders", async (t) => {
  const run = async (command) =>
    command === "uv"
      ? { code: 1, stdout: "", stderr: "  - NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required\n  - CLERK_SECRET_KEY is required\n  - OTHER_VAR is required\n", missing: false }
      : { code: 0, stderr: "", stdout: 'WEBCONFIG:{"ok":false,"problems":["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required"]}\n' };
  const result = await runPreflight({ root: withRoot(t, ""), env: {}, childEnv: {}, run });
  assert.equal(result.ok, false);
  assert.equal(result.problems.length, 2);
  assert.match(result.problems[0], /^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY are not set: paste your Clerk development keys/);
  assert.doesNotMatch(result.problems[0], /replace_me|the old/);
  assert.equal(result.problems[1], "OTHER_VAR is required");
});

test("real keys pass without a warning, and both apps' problems are collected once each", async (t) => {
  const env = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: realPublishable, CLERK_SECRET_KEY: `sk_${"test"}_${"c".repeat(30)}` };
  const root = withRoot(t, "");
  const clean = await runPreflight({ root, env, childEnv: env, run: passing });
  assert.deepEqual(clean, { ok: true, problems: [], warnings: [] });

  const run = async (command) =>
    command === "uv"
      ? { code: 1, stdout: "", stderr: "  - SHARED_VAR is malformed\n  - API_ONLY is required\n", missing: false }
      : { code: 0, stderr: "", stdout: 'WEBCONFIG:{"ok":false,"problems":["SHARED_VAR is malformed","WEB_ONLY is required"]}\n' };
  const broken = await runPreflight({ root, env, childEnv: env, run });
  assert.deepEqual(broken.problems, ["SHARED_VAR is malformed", "API_ONLY is required", "WEB_ONLY is required"]);
});

test("the web check runs the web app's own validators through Next's config loader", async () => {
  const { capture } = await import("../lib/preflight.mjs");
  const base = {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    NEXT_TELEMETRY_DISABLED: "1",
    APP_ENV: "local",
    ANALYTICS_MODE: "disabled",
    ERROR_REPORTING_MODE: "disabled",
    API_ORIGIN: "http://127.0.0.1:8000",
    CLERK_JWT_KEY: "",
    STRIPE_CUSTOM_DOMAIN: "",
    SENTRY_TRACES_SAMPLE_RATE: "0",
    NEXT_PUBLIC_POSTHOG_HOST: "https://us.i.posthog.com",
    NEXT_PUBLIC_POSTHOG_KEY: "",
    NEXT_PUBLIC_SENTRY_DSN: "",
  };
  const check = (clerk) =>
    capture(process.execPath, [path.join(repo, "scripts/lib/check-web-config.mjs"), path.join(repo, "apps/web")], {
      cwd: path.join(repo, "apps/web"),
      env: { ...base, ...clerk },
    }).then(webProblems);
  const [placeholder, legacy] = await Promise.all([
    check({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY, CLERK_SECRET_KEY: PLACEHOLDER_SECRET_KEY }),
    check({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_replace_me", CLERK_SECRET_KEY: "sk_test_replace_me" }),
  ]);
  assert.deepEqual(placeholder, []);
  assert.deepEqual(legacy, [
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder (copy it from Clerk Dashboard > API keys)",
    "CLERK_SECRET_KEY is malformed or still the .env.example placeholder",
  ]);
});
