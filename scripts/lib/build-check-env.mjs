/**
 * The environment of `pnpm build:check` (scripts/build-check.mjs): an account-free build that is the same on
 * every machine. It gets the placeholder Clerk publishable key that .env.example ships, a synthetic secret key
 * and a synthetic public key (CLERK_JWT_KEY, which the proxy bundle inlines) made up for the run, so the bundle
 * scan proves the server-only values the web build can see stay out of the browser bundle. Telemetry is off,
 * APP_ENV is local, and SKIP_ROOT_ENV_FILE keeps next.config.ts from reading the root .env, so a developer's
 * settings (API_ORIGIN, a PostHog host) cannot change the build.
 */
import { generateKeyPairSync, randomBytes } from "node:crypto";

import { PLACEHOLDER_PUBLISHABLE_KEY } from "./clerk.mjs";

export function buildCheckEnv(base = process.env) {
  return {
    ...base,
    APP_ENV: "local",
    ANALYTICS_MODE: "disabled",
    ERROR_REPORTING_MODE: "disabled",
    SKIP_ROOT_ENV_FILE: "1",
    CLERK_JWT_KEY: generateKeyPairSync("rsa", { modulusLength: 2048 }).publicKey.export({ type: "spki", format: "pem" }),
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY,
    CLERK_SECRET_KEY: `sk_test_${randomBytes(24).toString("hex")}`,
  };
}
