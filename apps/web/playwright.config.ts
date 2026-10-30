import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { RUNNER_VARIABLES, isSandboxRun, sandboxProblems, sandboxUnavailable } from "./e2e/support/sandbox";
import { loadEnvFiles } from "./src/lib/env-files";

/**
 * End-to-end tests run against a production build of the web app and a local API.
 *
 * Two tiers, chosen on the command line (`E2E_SANDBOX=1 pnpm test:e2e`; never from .env, which this file
 * reads only once that switch is on):
 * - Account-free (default, what CI runs): no external accounts. The HTTP checks (e2e/http.spec.ts) use only
 *   the request fixture; the browser checks (e2e/pages.spec.ts) run with the placeholder Clerk keys of
 *   .env.example and clerk-js blocked (see e2e/support/browser.ts). Mocked evidence.
 * - Sandbox: a real Clerk development instance. The Clerk keys come from the root .env, and the run refuses
 *   to start, naming the variable, when they are missing, placeholders, or production keys. The clerk-setup
 *   project (e2e/clerk.setup.ts) creates the test user and proves one sign-in first; the signed-in flows
 *   (e2e/authenticated.spec.ts) depend on it. Sandbox evidence, never live.
 *
 * Databases:
 * - Account-free runs make no database calls. Their API points at a database name nothing creates (or at
 *   E2E_DATABASE_URL), never the development database, so a test that starts using the database fails
 *   instead of writing there.
 * - Sandbox runs take the API's settings from the root .env, DATABASE_URL included, except the origins: the
 *   page is served at 127.0.0.1:3100, so that is the origin its session tokens carry and both the API and the
 *   web proxy must authorize (process values win over .env). The test user's rows, a project (deleted again), and a
 *   billing-customer mapping are written to that database, which is neither isolated nor dropped (DB-08):
 *   point .env at a disposable local database for those runs.
 */
const root = path.resolve(import.meta.dirname, "../..");
// The placeholder pair .env.example ships: well formed, and naming a host (.invalid) nobody can register.
const placeholderKey = `pk_test_${Buffer.from("clerk.not-configured-yet.invalid$").toString("base64")}`;
const sandbox = isSandboxRun(process.env);
const webPort = 3100;
const apiPort = 8100;
// Rewrites are fixed at build time, so the web app is built with the e2e API origin.
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const webOrigin = `http://127.0.0.1:${webPort}`;

if (sandbox) {
  // The workers need the Clerk keys (testing token, sign-in token); the servers read the rest of the file
  // themselves, and the web server inherits this process's environment, so only these names are loaded.
  loadEnvFiles(root, (name) => RUNNER_VARIABLES.includes(name), process.env);
  const problems = sandboxProblems(process.env);
  if (problems.length) throw sandboxUnavailable(problems);
}

// Browser projects wait for the sandbox setup; the account-free run has none.
const setup = sandbox ? ["clerk-setup"] : [];

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  // The sandbox tests share one Clerk user and one workspace, so projects must not run side by side.
  workers: sandbox ? 1 : undefined,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  // Real Clerk and Stripe answer more slowly than a local server.
  timeout: sandbox ? 60_000 : 30_000,
  expect: { timeout: sandbox ? 15_000 : 5_000 },
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
  },
  projects: [
    ...(sandbox ? [{ name: "clerk-setup", testMatch: /clerk\.setup\.ts/ }] : []),
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, dependencies: setup },
    { name: "mobile", use: { ...devices["Pixel 7"] }, dependencies: setup },
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
        // Sandbox: the proxy checks the session token's azp claim against these, and next.config.ts inlines
        // them at build time, so the web process (build and start) needs the page's origin like the API does.
        // Account-free runs are "mocked" evidence: pinned like the API above, so a developer's root .env
        // cannot switch telemetry on or supply real keys (process values win over .env). APP_ENV=test
        // has no parties and no check.
        ...(sandbox
          ? { APP_BASE_URL: webOrigin, CLERK_AUTHORIZED_PARTIES: webOrigin }
          : {
              APP_ENV: "test",
              ANALYTICS_MODE: "disabled",
              ERROR_REPORTING_MODE: "disabled",
              NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: placeholderKey,
              CLERK_SECRET_KEY: "sk_test_clerk_not_configured_yet",
              CLERK_JWT_KEY: "",
            }),
      },
    },
  ],
});
