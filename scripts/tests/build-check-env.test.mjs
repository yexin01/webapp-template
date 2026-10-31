import assert from "node:assert/strict";
import test from "node:test";

import { buildCheckEnv } from "../lib/build-check-env.mjs";
import { PLACEHOLDER_PUBLISHABLE_KEY } from "../lib/clerk.mjs";

const developer = {
  PATH: "/usr/bin",
  APP_ENV: "production",
  ANALYTICS_MODE: "live",
  ERROR_REPORTING_MODE: "live",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_live_developer",
  CLERK_SECRET_KEY: "sk_live_developer",
  CLERK_JWT_KEY: "developer-key",
};

test("the build is account-free and does not depend on the developer's .env or shell", () => {
  const env = buildCheckEnv(developer);
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(env.APP_ENV, "local");
  assert.equal(env.ANALYTICS_MODE, "disabled");
  assert.equal(env.ERROR_REPORTING_MODE, "disabled");
  assert.equal(env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, PLACEHOLDER_PUBLISHABLE_KEY);
  // next.config.ts reads the root .env unless this is 1 (apps/web/src/next-config.test.ts).
  assert.equal(env.SKIP_ROOT_ENV_FILE, "1");
});

test("the Clerk keys are made up for the run, so the bundle scan has values to look for that are not the developer's", () => {
  const [first, second] = [buildCheckEnv(developer), buildCheckEnv(developer)];
  assert.match(first.CLERK_SECRET_KEY, /^sk_test_[0-9a-f]{48}$/);
  assert.match(first.CLERK_JWT_KEY, /^-----BEGIN PUBLIC KEY-----/);
  assert.notEqual(first.CLERK_SECRET_KEY, developer.CLERK_SECRET_KEY);
  assert.notEqual(first.CLERK_JWT_KEY, developer.CLERK_JWT_KEY);
  assert.notEqual(first.CLERK_SECRET_KEY, second.CLERK_SECRET_KEY);
});
