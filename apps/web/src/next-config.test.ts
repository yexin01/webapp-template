// @vitest-environment node
import { PHASE_PRODUCTION_BUILD } from "next/constants";

/**
 * What next.config.ts hands the rest of the app. The proxy may not read the server-only variables the
 * authorized parties come from (ENV-07), so the config passes them down as a NEXT_PUBLIC value, which
 * Next.js inlines into the proxy at build time. The validator and the files are replaced: this is about
 * the wiring, not the rules (env.server.test.ts, env-cases.test.ts). The build options handed to Sentry are
 * the real ones, so the test below sees what `next build` would pass.
 */
const loadServerEnv = vi.hoisted(() => vi.fn());
const withSentryConfig = vi.hoisted(() =>
  vi.fn<(config: unknown, options?: object) => unknown>((config) => config),
);
vi.mock("@sentry/nextjs/config", () => ({ withSentryConfig }));
vi.mock("./lib/env-files", () => ({ loadEnvFiles: () => [], strayEnvFiles: () => [] }));
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
