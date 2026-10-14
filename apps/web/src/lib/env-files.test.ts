// @vitest-environment node
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { loadEnvFiles } from "./env-files";
import { isWebVariable } from "./env.server";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), "web-env-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe("loading the shared root .env", () => {
  it("loads only the web app's variables", () => {
    writeFileSync(
      path.join(dir, ".env"),
      [
        "APP_ENV=local",
        "export ANALYTICS_MODE=disabled",
        "NEXT_PUBLIC_POSTHOG_HOST=https://eu.i.posthog.com # comment",
        "STRIPE_SECRET_KEY=sk_test_not_a_real_secret",
        "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54329/app",
        "LLM_API_KEY=not_a_real_secret",
        "CRON_SECRET=not_a_real_secret_but_32_characters",
      ].join("\n"),
    );
    const target: Record<string, string | undefined> = {};
    const loaded = loadEnvFiles(dir, "development", isWebVariable, target);
    expect(target).toEqual({
      APP_ENV: "local",
      ANALYTICS_MODE: "disabled",
      NEXT_PUBLIC_POSTHOG_HOST: "https://eu.i.posthog.com",
    });
    expect(loaded.sort()).toEqual(["ANALYTICS_MODE", "APP_ENV", "NEXT_PUBLIC_POSTHOG_HOST"]);
  });

  it("follows Next.js precedence and never overrides the process environment", () => {
    writeFileSync(
      path.join(dir, ".env"),
      "APP_ENV=local\nAPI_ORIGIN=http://from-env\nANALYTICS_MODE=disabled\n",
    );
    writeFileSync(path.join(dir, ".env.local"), "API_ORIGIN=http://from-env-local\n");
    writeFileSync(path.join(dir, ".env.production"), "API_ORIGIN=http://from-env-production\n");
    writeFileSync(path.join(dir, ".env.development.local"), "ANALYTICS_MODE=sandbox\n");
    const target: Record<string, string | undefined> = { APP_ENV: "test" };
    loadEnvFiles(dir, "development", isWebVariable, target);
    expect(target).toEqual({
      APP_ENV: "test",
      API_ORIGIN: "http://from-env-local",
      ANALYTICS_MODE: "sandbox",
    });
  });

  it("skips .env.local in test mode and tolerates missing files", () => {
    writeFileSync(path.join(dir, ".env.local"), "APP_ENV=local\n");
    const target: Record<string, string | undefined> = {};
    expect(loadEnvFiles(dir, "test", isWebVariable, target)).toEqual([]);
    expect(target).toEqual({});
  });
});
