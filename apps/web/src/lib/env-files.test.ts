// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { loadEnvFiles, strayEnvFiles } from "./env-files";
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
        "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/app",
        "LLM_API_KEY=not_a_real_secret",
        "CRON_SECRET=not_a_real_secret_but_32_characters",
      ].join("\n"),
    );
    const target: Record<string, string | undefined> = {};
    const loaded = loadEnvFiles(dir, isWebVariable, target);
    expect(target).toEqual({
      APP_ENV: "local",
      ANALYTICS_MODE: "disabled",
      NEXT_PUBLIC_POSTHOG_HOST: "https://eu.i.posthog.com",
    });
    expect(loaded.sort()).toEqual(["ANALYTICS_MODE", "APP_ENV", "NEXT_PUBLIC_POSTHOG_HOST"]);
  });

  it("reads only the root .env, like the API and the bundle scan, and never overrides the process environment", () => {
    writeFileSync(
      path.join(dir, ".env"),
      "APP_ENV=local\nAPI_ORIGIN=http://from-env\nANALYTICS_MODE=disabled\n",
    );
    // The API and scripts/check-bundle-secrets.mjs read none of these, so the web app must not either.
    writeFileSync(
      path.join(dir, ".env.local"),
      "API_ORIGIN=http://from-env-local\nERROR_REPORTING_MODE=live\n",
    );
    writeFileSync(path.join(dir, ".env.production"), "API_ORIGIN=http://from-env-production\n");
    writeFileSync(path.join(dir, ".env.development.local"), "ANALYTICS_MODE=sandbox\n");
    writeFileSync(path.join(dir, ".env 2"), "ANALYTICS_MODE=live\n");
    const target: Record<string, string | undefined> = { APP_ENV: "test" };
    loadEnvFiles(dir, isWebVariable, target);
    expect(target).toEqual({
      APP_ENV: "test",
      API_ORIGIN: "http://from-env",
      ANALYTICS_MODE: "disabled",
    });
  });

  it("tolerates a missing .env", () => {
    const target: Record<string, string | undefined> = {};
    expect(loadEnvFiles(dir, isWebVariable, target)).toEqual([]);
    expect(target).toEqual({});
  });
});

describe("environment files that would split the configuration", () => {
  let root: string;
  let web: string;
  beforeEach(() => {
    root = dir;
    web = path.join(dir, "apps", "web");
    mkdirSync(web, { recursive: true });
  });

  it("reports nothing for the root .env, .env.example, and synced copies", () => {
    for (const file of [
      ".env",
      ".env.example",
      ".env 2",
      ".env.local 2",
      ".env.local~",
      ".envrc",
      ".env.staging",
    ]) {
      writeFileSync(path.join(root, file), "X=1\n");
    }
    writeFileSync(path.join(web, ".env.example"), "X=1\n");
    writeFileSync(path.join(web, ".env 2"), "X=1\n");
    expect(strayEnvFiles(root, web)).toEqual([]);
  });

  it("reports a root .env.local and the mode files by name", () => {
    writeFileSync(path.join(root, ".env"), "X=1\n");
    const files = [
      ".env.local",
      ".env.development",
      ".env.development.local",
      ".env.production.local",
      ".env.test",
    ];
    for (const file of files) writeFileSync(path.join(root, file), "X=1\n");
    expect(strayEnvFiles(root, web)).toEqual(files);
  });

  it("reports every .env file of the web app, which Next.js would load itself, unfiltered", () => {
    for (const file of [".env", ".env.local", ".env.production"])
      writeFileSync(path.join(web, file), "X=1\n");
    expect(strayEnvFiles(root, web)).toEqual([
      "apps/web/.env",
      "apps/web/.env.local",
      "apps/web/.env.production",
    ]);
  });

  it("never reads a file's contents into its answer", () => {
    writeFileSync(path.join(root, ".env.local"), "CLERK_SECRET_KEY=CANARY\n");
    expect(JSON.stringify(strayEnvFiles(root, web))).not.toContain("CANARY");
  });
});
