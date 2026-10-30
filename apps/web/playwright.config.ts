import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against a production build of the web app and a local
 * API. Public-page tests need no external accounts. Signed-in flows
 * (e2e/authenticated.spec.ts) run only when a Clerk development instance and
 * test user are configured; they are sandbox tests.
 *
 * Databases:
 * - Account-free runs make no database calls. Their API points at a database
 *   name nothing creates (or at E2E_DATABASE_URL), never the development
 *   database, so a test that starts using the database fails instead of
 *   writing there.
 * - Sandbox runs (E2E_SANDBOX=1) take the API's settings from the root .env,
 *   DATABASE_URL included, except the origins: the page is served at 127.0.0.1:3100,
 *   so that is the origin its session tokens carry and the API must authorize
 *   (process values win over .env). The test user's rows, a project (deleted again),
 *   and a billing-customer mapping are written to that database, which is
 *   neither isolated nor dropped (DB-08): point .env at a disposable local
 *   database for those runs.
 */
const root = path.resolve(import.meta.dirname, "../..");
const placeholderKey = `pk_test_${Buffer.from("placeholder.clerk.accounts.dev$").toString("base64")}`;
const sandbox = Boolean(process.env.E2E_SANDBOX);
const webPort = 3100;
const apiPort = 8100;
// Rewrites are fixed at build time, so the web app is built with the e2e API origin.
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const webOrigin = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: `uv run uvicorn index:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: path.join(root, "apps/api"),
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      reuseExistingServer: false,
      env: sandbox
        ? { APP_BASE_URL: webOrigin, CLERK_AUTHORIZED_PARTIES: webOrigin }
        : {
            APP_ENV: "test",
            DATABASE_URL:
              process.env.E2E_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54329/app_e2e_unused",
            BILLING_MODE: "mock",
            LLM_MODE: "mock",
            ANALYTICS_MODE: "disabled",
            ERROR_REPORTING_MODE: "disabled",
          },
    },
    {
      command: `pnpm exec next build && pnpm exec next start --port ${webPort}`,
      timeout: 180_000,
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      env: {
        API_ORIGIN: apiOrigin,
        // Account-free runs are "mocked" evidence: pinned like the API above, so a
        // developer's root .env cannot switch telemetry on (process values win over .env).
        ...(sandbox
          ? {}
          : {
              APP_ENV: "test",
              ANALYTICS_MODE: "disabled",
              ERROR_REPORTING_MODE: "disabled",
              NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: placeholderKey,
              CLERK_SECRET_KEY: "sk_test_placeholder",
            }),
      },
    },
  ],
});
