// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * DB-08: account-free end-to-end runs never point the API at the shared
 * development database that .env.example configures.
 */
const repoRoot = path.resolve(import.meta.dirname, "../../..");

it("points the account-free e2e API at its own database, never the development one", async () => {
  vi.stubEnv("E2E_SANDBOX", "");
  vi.stubEnv("E2E_DATABASE_URL", undefined);
  const { default: config } = await import("../playwright.config");
  const api = [config.webServer].flat().find((server) => server?.command.includes("uvicorn"));
  const development = /^DATABASE_URL=(.+)$/m.exec(
    readFileSync(path.join(repoRoot, ".env.example"), "utf8"),
  )?.[1];

  const databaseOf = (url: string | undefined) => new URL(url ?? "").pathname;
  expect(development).toBeDefined();
  expect(api?.env?.APP_ENV).toBe("test");
  expect(databaseOf(api?.env?.DATABASE_URL)).not.toBe(databaseOf(development));
});

it("authorizes the origin the sandbox e2e page is served from, whatever the root .env lists", async () => {
  vi.stubEnv("E2E_SANDBOX", "1");
  vi.resetModules();
  const { default: config } = await import("../playwright.config");
  const servers = [config.webServer].flat();
  const api = servers.find((server) => server?.command.includes("uvicorn"));
  const web = servers.find((server) => server?.command.includes("next start"));
  const origin = "http://127.0.0.1:3100";
  // A session token carries the origin of the page that asked for it (the azp claim).
  expect(config.use?.baseURL).toBe(origin);
  expect(api?.env?.APP_BASE_URL).toBe(origin);
  expect(api?.env?.CLERK_AUTHORIZED_PARTIES).toBe(origin);
  expect(web?.command).toContain("--port 3100");
  // Everything else comes from the root .env, so a sandbox run exercises the developer's configuration.
  expect(api?.env?.APP_ENV).toBeUndefined();
  expect(api?.env?.DATABASE_URL).toBeUndefined();
});
