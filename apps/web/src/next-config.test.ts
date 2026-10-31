// @vitest-environment node
import { PHASE_PRODUCTION_BUILD } from "next/constants";

/**
 * What next.config.ts hands the rest of the app. The proxy may not read the server-only variables the
 * authorized parties and the Clerk key come from (ENV-07), so the config passes them down as NEXT_PUBLIC
 * values, which Next.js inlines into the proxy at build time. The validator and the files are replaced:
 * this is about the wiring, not the rules (env.server.test.ts, env-cases.test.ts).
 * The build options handed to Sentry are the real ones, so the test below sees what `next build` would pass.
 */
const loadServerEnv = vi.hoisted(() => vi.fn());
const withSentryConfig = vi.hoisted(() =>
  vi.fn<(config: unknown, options?: object) => unknown>((config) => config),
);
vi.mock("@sentry/nextjs/config", () => ({ withSentryConfig }));
const loadEnvFiles = vi.hoisted(() => vi.fn<() => string[]>(() => []));
vi.mock("./lib/env-files", () => ({ loadEnvFiles, strayEnvFiles: () => [] }));
vi.mock("./lib/env.server", async (importOriginal) => ({
  ConfigError: class extends Error {},
  isWebVariable: () => false,
  loadServerEnv,
  sentryBuildOptions: (await importOriginal<typeof import("./lib/env.server")>()).sentryBuildOptions,
  sourceMapUploadProblems: () => [],
}));

const serverEnv = {
  appEnv: "preview",
  apiOrigin: "https://api.example.com",
  clerkPublishableKey: "",
  clerkAuthorizedParties: ["https://app.example.com", "https://preview.example.com"],
  clerkJwtKey: "",
  stripeCustomDomain: null,
  analytics: { mode: "disabled", enabled: false, host: "https://us.i.posthog.com" },
  errorReporting: { mode: "disabled", enabled: false, dsn: "", tracesSampleRate: 0 },
};

async function configFor(env: object) {
  loadServerEnv.mockReturnValue(env);
  const { default: nextConfig } = await import("../next.config");
  return nextConfig(PHASE_PRODUCTION_BUILD);
}

describe("next.config.ts", () => {
  it("passes the origins sessions are accepted from to the proxy, as a comma-separated NEXT_PUBLIC value", async () => {
    const config = await configFor(serverEnv);
    expect(config.env?.NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES).toBe(
      "https://app.example.com,https://preview.example.com",
    );
  });

  it("passes an empty value where there are none (automated tests), which the proxy reads as no check", async () => {
    const config = await configFor({ ...serverEnv, clerkAuthorizedParties: [] });
    expect(config.env?.NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES).toBe("");
  });

  it("passes the Clerk instance's public key to the proxy as a NEXT_PUBLIC value", async () => {
    const pem = "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----";
    const config = await configFor({ ...serverEnv, clerkJwtKey: pem });
    expect(config.env?.NEXT_PUBLIC_CLERK_JWT_KEY).toBe(pem);
  });

  it("passes an empty value where CLERK_JWT_KEY is unset, which the proxy reads as no key", async () => {
    const config = await configFor(serverEnv);
    expect(config.env?.NEXT_PUBLIC_CLERK_JWT_KEY).toBe("");
  });
});

/**
 * ADR-0004 §11: browser events go straight to Sentry. `tunnelRoute` makes Sentry add a same-origin rewrite that
 * forwards every header, so the relay would receive the visitor's cookies, Clerk's session cookie included.
 * Turning it on is a decision: change this test and the ADR together.
 */
describe("the telemetry relay (ADR-0004 §11)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ["without a source-map upload token", undefined],
    ["with a source-map upload token", "CANARYtoken0123456789"],
  ])("does not enable Sentry's tunnelRoute %s", async (_case, token) => {
    vi.stubEnv("SENTRY_AUTH_TOKEN", token ?? "");
    vi.resetModules();
    withSentryConfig.mockClear();
    await configFor(serverEnv);

    expect(withSentryConfig).toHaveBeenCalledOnce();
    const options: { tunnelRoute?: unknown } | undefined = withSentryConfig.mock.calls[0]?.[1];
    expect(options, "next.config.ts hands withSentryConfig its build options").toBeDefined();
    expect(
      options?.tunnelRoute,
      "tunnelRoute is a same-origin relay that receives the visitor's cookies: see ADR-0004 §11",
    ).toBeFalsy();
  });
});

/**
 * `pnpm build:check` is the account-free build CI runs and a fresh clone can run: it must not change with the
 * developer's .env (an API_ORIGIN there lands in the routes manifest, a PostHog host in the CSP). It sets
 * SKIP_ROOT_ENV_FILE=1 (scripts/lib/build-check-env.mjs); every other build and server reads the file.
 */
describe("the repository-root .env", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ["is read by default", undefined, 1],
    ["is read when the switch is anything but 1", "0", 1],
    ["is not read when SKIP_ROOT_ENV_FILE=1 (pnpm build:check)", "1", 0],
  ])("%s", async (_case, switchValue, reads) => {
    vi.stubEnv("SKIP_ROOT_ENV_FILE", switchValue);
    vi.resetModules();
    loadEnvFiles.mockClear();
    await configFor(serverEnv);

    expect(loadEnvFiles).toHaveBeenCalledTimes(reads);
  });
});
