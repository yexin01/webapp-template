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
