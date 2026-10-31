import assert from "node:assert/strict";
import test from "node:test";

import { buildCheckEnv } from "../lib/build-check-env.mjs";
import { PLACEHOLDER_PUBLISHABLE_KEY } from "../lib/clerk.mjs";

// A developer's shell: the machine's variables, the app's settings that change a build, and secrets of other tools.
const developer = {
  PATH: "/usr/bin",
  HOME: "/home/dev",
  TMPDIR: "/tmp",
  COREPACK_HOME: "/home/dev/.corepack",
  APP_ENV: "production",
  ANALYTICS_MODE: "live",
  ERROR_REPORTING_MODE: "live",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_live_developer",
  CLERK_SECRET_KEY: "sk_live_developer",
  CLERK_JWT_KEY: "developer-key",
  SENTRY_AUTH_TOKEN: "developer-token",
  SENTRY_ORG: "developer-org",
  SENTRY_PROJECT: "developer-project",
  SENTRY_DSN: "https://key@example.invalid/1",
  NEXT_PUBLIC_SENTRY_DSN: "https://key@example.invalid/1",
  NEXT_PUBLIC_POSTHOG_KEY: "phc_developer",
  NEXT_PUBLIC_APP_ENV: "production",
  API_ORIGIN: "https://api.developer.invalid",
  APP_BASE_URL: "https://developer.invalid",
  VERCEL: "1",
  VERCEL_ENV: "production",
  VERCEL_URL: "developer.vercel.invalid",
  NODE_ENV: "development",
  STRIPE_SECRET_KEY: "sk_live_stripe",
  AWS_SECRET_ACCESS_KEY: "developer-aws",
  GITHUB_TOKEN: "developer-github",
};

test("the build is account-free and does not depend on the developer's .env or shell", () => {
  const env = buildCheckEnv(developer);
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(env.APP_ENV, "local");
  assert.equal(env.ANALYTICS_MODE, "disabled");
  assert.equal(env.ERROR_REPORTING_MODE, "disabled");
  assert.equal(env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, PLACEHOLDER_PUBLISHABLE_KEY);
  assert.equal(env.NEXT_TELEMETRY_DISABLED, "1");
  // next.config.ts reads the root .env unless this is 1 (apps/web/src/next-config.test.ts).
  assert.equal(env.SKIP_ROOT_ENV_FILE, "1");
});

test("what the shell exports reaches the build only if it is a machine variable", () => {
  const env = buildCheckEnv(developer);
  // Source-map upload follows SENTRY_AUTH_TOKEN (sentryBuildOptions), and the origins and NEXT_PUBLIC_* change the output.
  for (const name of Object.keys(env)) {
    assert.doesNotMatch(name, /^(SENTRY_|VERCEL)/, `${name} reached the build`);
    if (name.startsWith("NEXT_PUBLIC_")) assert.equal(name, "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", `${name} reached the build`);
  }
  for (const name of ["API_ORIGIN", "APP_BASE_URL", "NODE_ENV", "STRIPE_SECRET_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"]) {
    assert.equal(name in env, false, `${name} reached the build`);
  }
  // Exactly the machine variables and the values the script sets.
  assert.deepEqual(Object.keys(env).sort(), [
    "ANALYTICS_MODE",
    "APP_ENV",
    "CLERK_JWT_KEY",
    "CLERK_SECRET_KEY",
    "COREPACK_HOME",
    "ERROR_REPORTING_MODE",
    "HOME",
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    "NEXT_TELEMETRY_DISABLED",
    "PATH",
    "SKIP_ROOT_ENV_FILE",
    "TMPDIR",
  ]);
  assert.equal(env.HOME, "/home/dev");
});

test("Windows folds the case of variable names, so the machine variables are matched that way", () => {
  const env = buildCheckEnv({ Path: "C:\\bin", SystemRoot: "C:\\Windows", ComSpec: "C:\\Windows\\cmd.exe", Sentry_Auth_Token: "x" });
  assert.equal(env.Path, "C:\\bin");
  assert.equal(env.SystemRoot, "C:\\Windows");
  assert.equal(env.ComSpec, "C:\\Windows\\cmd.exe");
  assert.equal("Sentry_Auth_Token" in env, false);
});

test("the Clerk keys are made up for the run, so the bundle scan has values to look for that are not the developer's", () => {
  const [first, second] = [buildCheckEnv(developer), buildCheckEnv(developer)];
  assert.match(first.CLERK_SECRET_KEY, /^sk_test_[0-9a-f]{48}$/);
  assert.match(first.CLERK_JWT_KEY, /^-----BEGIN PUBLIC KEY-----/);
  assert.notEqual(first.CLERK_SECRET_KEY, developer.CLERK_SECRET_KEY);
  assert.notEqual(first.CLERK_JWT_KEY, developer.CLERK_JWT_KEY);
  assert.notEqual(first.CLERK_SECRET_KEY, second.CLERK_SECRET_KEY);
});
