// @vitest-environment node
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

/**
 * DB-08: account-free end-to-end runs never point the API at the shared development database that
 * .env.example configures. The sandbox run reads the root .env for three variables only, authorizes the origin
 * its pages are served from, and refuses to start without usable Clerk development keys.
 */
const repoRoot = path.resolve(import.meta.dirname, "../../..");

type Include = (name: string) => boolean;
const loader = vi.hoisted(() =>
  vi.fn<(dir: string, include: Include, target: unknown) => string[]>(() => []),
);
// The developer's real .env must never be read by a unit test.
vi.mock("./lib/env-files", () => ({ loadEnvFiles: loader }));

const exampleValue = (name: string) =>
  new RegExp(`^${name}=(.+)$`, "m").exec(readFileSync(path.join(repoRoot, ".env.example"), "utf8"))?.[1];
// Built at run time: no secret-shaped literal in the source.
const developmentKeys = {
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: `pk_${"test"}_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`,
  CLERK_SECRET_KEY: `sk_${"test"}_${"c".repeat(30)}`,
};

beforeEach(() => {
  loader.mockClear();
  vi.resetModules();
  vi.stubEnv("E2E_SANDBOX", "");
  vi.stubEnv("E2E_DATABASE_URL", undefined);
  vi.stubEnv("E2E_CLERK_USER_EMAIL", undefined);
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", undefined);
  vi.stubEnv("CLERK_SECRET_KEY", undefined);
});
afterEach(() => vi.unstubAllEnvs());

async function loadConfig() {
  const { default: config } = await import("../playwright.config");
  const servers = [config.webServer].flat();
  return {
    config,
    api: servers.find((server) => server?.command.includes("uvicorn")),
    web: servers.find((server) => server?.command.includes("next start")),
    projects: Object.fromEntries((config.projects ?? []).map((project) => [project.name, project])),
  };
}

describe("account-free run", () => {
  it("points the API at its own database, never the development one", async () => {
    const { api } = await loadConfig();
    const development = exampleValue("DATABASE_URL");

    const databaseOf = (url: string | undefined) => new URL(url ?? "").pathname;
    expect(development).toBeDefined();
    expect(api?.env?.APP_ENV).toBe("test");
    expect(databaseOf(api?.env?.DATABASE_URL)).not.toBe(databaseOf(development));
  });

  it("builds the web app with the placeholder Clerk pair .env.example ships, and reads no .env", async () => {
    const { web, projects, config } = await loadConfig();
    // A host nobody can register: if the browser tests ever let a request out, it still reaches nothing.
    expect(web?.env?.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY).toBe(
      exampleValue("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"),
    );
    expect(web?.env?.CLERK_SECRET_KEY).toBe(exampleValue("CLERK_SECRET_KEY"));
    expect(loader).not.toHaveBeenCalled();
    expect(Object.keys(projects)).toEqual(["desktop", "mobile"]);
    expect(config.workers).toBeUndefined();
  });

  it.each(["0", "true", "yes"])(
    "treats E2E_SANDBOX=%s as no switch: only 1 turns the sandbox on",
    async (value) => {
      vi.stubEnv("E2E_SANDBOX", value);
      const { api, projects } = await loadConfig();
      expect(api?.env?.APP_ENV).toBe("test");
      expect(Object.keys(projects)).toEqual(["desktop", "mobile"]);
    },
  );
});

describe("sandbox run (E2E_SANDBOX=1)", () => {
  beforeEach(() => {
    vi.stubEnv("E2E_SANDBOX", "1");
    for (const [name, value] of Object.entries(developmentKeys)) vi.stubEnv(name, value);
  });

  it("authorizes the origin the page is served from, whatever the root .env lists", async () => {
    const { config, api, web } = await loadConfig();
    const origin = "http://127.0.0.1:3100";
    // A session token carries the origin of the page that asked for it (the azp claim).
    expect(config.use?.baseURL).toBe(origin);
    expect(api?.env?.APP_BASE_URL).toBe(origin);
    expect(api?.env?.CLERK_AUTHORIZED_PARTIES).toBe(origin);
    expect(web?.command).toContain("--port 3100");
    // Everything else comes from the root .env, so a sandbox run exercises the developer's configuration.
    expect(api?.env?.APP_ENV).toBeUndefined();
    expect(api?.env?.DATABASE_URL).toBeUndefined();
    expect(web?.env?.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY).toBeUndefined();
  });

  it("runs the setup project first, and one test at a time because they share one Clerk user", async () => {
    const { config, projects } = await loadConfig();
    expect(Object.keys(projects)).toEqual(["clerk-setup", "desktop", "mobile"]);
    expect(projects["clerk-setup"]?.testMatch).toEqual(/clerk\.setup\.ts/);
    expect(projects.desktop?.dependencies).toEqual(["clerk-setup"]);
    expect(projects.mobile?.dependencies).toEqual(["clerk-setup"]);
    expect(config.workers).toBe(1);
  });

  it("loads the Clerk keys and the test user's address from the root .env, and nothing else", async () => {
    await loadConfig();
    expect(loader).toHaveBeenCalledTimes(1);
    const [dir, include, target] = loader.mock.calls[0] ?? [];
    expect(dir).toBe(repoRoot);
    expect(target).toBe(process.env);

    // The web server inherits this process's environment: a secret loaded here would reach it.
    const { loadEnvFiles } = await vi.importActual<{
      loadEnvFiles: (dir: string, include: Include, target: Record<string, string | undefined>) => string[];
    }>("./lib/env-files");
    const scratch = mkdtempSync(path.join(tmpdir(), "e2e-env-"));
    try {
      writeFileSync(
        path.join(scratch, ".env"),
        [
          "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_from_file",
          "CLERK_SECRET_KEY=sk_test_from_file",
          "E2E_CLERK_USER_EMAIL=someone+clerk_test@example.com",
          "E2E_SANDBOX=1",
          "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/app",
          "STRIPE_SECRET_KEY=sk_test_from_file",
          "LLM_API_KEY=from_file",
          "APP_ENV=local",
        ].join("\n"),
      );
      const loaded: Record<string, string | undefined> = {};
      loadEnvFiles(scratch, include as Include, loaded);
      expect(Object.keys(loaded).sort()).toEqual([
        "CLERK_SECRET_KEY",
        "E2E_CLERK_USER_EMAIL",
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
      ]);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });

  it.each([
    ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", undefined, /NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set/],
    ["CLERK_SECRET_KEY", undefined, /CLERK_SECRET_KEY is not set/],
    [
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
      exampleValue("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"),
      /NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the \.env\.example placeholder/,
    ],
  ])("refuses to start, before building anything, when %s is wrong", async (name, value, message) => {
    vi.stubEnv(name, value);
    await expect(import("../playwright.config")).rejects.toThrow(message);
  });

  it("names every problem at once and never prints a value", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", `sk_${"live"}_${"d".repeat(30)}`);
    const error = await import("../playwright.config").catch((reason: Error) => reason);
    expect(error).toBeInstanceOf(Error);
    const text = (error as Error).message;
    expect(text).toMatch(/E2E_SANDBOX=1\) cannot start/);
    expect(text).toMatch(/NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set/);
    expect(text).toMatch(/CLERK_SECRET_KEY is a production key/);
    expect(text).not.toContain("d".repeat(30));
  });
});
