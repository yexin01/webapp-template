import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against a production build of the web app and a local
 * API using an isolated test database. Public-page tests need no external
 * accounts. Signed-in flows (e2e/authenticated.spec.ts) run only when a Clerk
 * development instance and test user are configured; they are sandbox tests.
 */
const root = path.resolve(import.meta.dirname, "../..");
const placeholderKey = `pk_test_${Buffer.from("placeholder.clerk.accounts.dev$").toString("base64")}`;
const sandbox = Boolean(process.env.E2E_SANDBOX);
const webPort = 3100;
const apiPort = 8100;
// Rewrites are fixed at build time, so the web app is built with the e2e API origin.
const apiOrigin = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
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
        ? {}
        : {
            APP_ENV: "test",
            DATABASE_URL:
              process.env.E2E_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54329/app",
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
