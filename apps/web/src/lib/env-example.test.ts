// @vitest-environment node
import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { clerkFrontendApiOrigin } from "./csp";
import { loadEnvFiles } from "./env-files";
import { isWebVariable, loadServerEnv } from "./env.server";

const example = path.resolve(import.meta.dirname, "../../../../.env.example");

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), "web-env-example-"));
  copyFileSync(example, path.join(dir, ".env"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe("the shipped .env.example", () => {
  const load = () => {
    const env: Record<string, string | undefined> = {};
    loadEnvFiles(dir, "development", isWebVariable, env);
    return env;
  };

  it.each([false, true])("builds and serves the web app without accounts (serving: %s)", (serving) => {
    expect(() => loadServerEnv(load(), { serving })).not.toThrow();
  });

  it("ships a Clerk pair that names a host nobody can register, so it can never match a real instance", () => {
    const key = load().NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
    const origin = clerkFrontendApiOrigin(key);
    expect(origin).not.toBeNull();
    expect(new URL(origin ?? "").hostname.endsWith(".invalid")).toBe(true);
    expect(load().CLERK_SECRET_KEY).toMatch(/not_configured/);
  });
});
