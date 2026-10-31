// @vitest-environment node
import { PHASE_PRODUCTION_BUILD } from "next/constants";

import { buildContentSecurityPolicy } from "./lib/csp";

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
 * SEC-02: what every response carries. The policy's own content is csp.test.ts; this is the wiring around it
 * (which providers and which build mode it is built for) and the headers that are not the policy. Strict-Transport-Security
 * is sent by production builds only, for the host itself: a sibling subdomain that is still served over plain HTTP
 * keeps working (ADR-0004 §24).
 */
describe("the security headers (SEC-02)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  const clerkPublishableKey = `pk_test_${btoa("example-app.clerk.accounts.dev$")}`;
  const POSTHOG = "https://eu.i.posthog.com";
  const DSN = "https://public@o123.ingest.de.sentry.io/456";
  const withTelemetry = {
    ...serverEnv,
    clerkPublishableKey,
    analytics: { mode: "enabled", enabled: true, host: POSTHOG },
    errorReporting: { mode: "enabled", enabled: true, dsn: DSN, tracesSampleRate: 0 },
  };
  // Switched off, with the settings still in place: a policy built from them anyway would let their hosts in.
  const withoutTelemetry = {
    ...withTelemetry,
    analytics: { mode: "disabled", enabled: false, host: POSTHOG },
    errorReporting: { mode: "disabled", enabled: false, dsn: DSN, tracesSampleRate: 0 },
  };

  /** The headers of a build in `mode`, by name; every path gets the same set. */
  async function headersOf(mode: "production" | "development", env: object): Promise<Record<string, string>> {
    vi.stubEnv("NODE_ENV", mode);
    vi.resetModules();
    const config = await configFor(env);
    const rules = await config.headers?.();
    expect(
      rules?.map((rule) => rule.source),
      "one rule, for every path",
    ).toEqual(["/:path*"]);
    return Object.fromEntries((rules?.[0]?.headers ?? []).map(({ key, value }) => [key, value]));
  }

  const NOT_THE_POLICY = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  };

  it("sends the policy and these headers from a production build, with HSTS for the host alone", async () => {
    expect(
      await headersOf("production", withoutTelemetry),
      "a security header is missing, added, or has another value (HSTS covers this host only: ADR-0004 §24)",
    ).toEqual({
      "Content-Security-Policy": expect.any(String),
      ...NOT_THE_POLICY,
      "Strict-Transport-Security": "max-age=63072000",
    });
  });

  it("sends the same without HSTS from a development server, which is served over http", async () => {
    expect(
      await headersOf("development", withoutTelemetry),
      "a security header is missing, added, or has another value (a development server sends no HSTS)",
    ).toEqual({
      "Content-Security-Policy": expect.any(String),
      ...NOT_THE_POLICY,
    });
  });

  it.each([
    ["a production build with PostHog and Sentry", "production", withTelemetry, POSTHOG, DSN, false],
    ["a production build without them", "production", withoutTelemetry, undefined, undefined, false],
    ["a development server with PostHog and Sentry", "development", withTelemetry, POSTHOG, DSN, true],
  ] as const)(
    "builds the policy for %s from what is configured",
    async (_case, mode, env, posthogHost, sentryDsn, isDev) => {
      const headers = await headersOf(mode, env);
      expect(
        headers["Content-Security-Policy"],
        "the policy sent is not the one built for the configured providers and the build mode",
      ).toBe(buildContentSecurityPolicy({ clerkPublishableKey, posthogHost, sentryDsn, isDev }));
    },
  );
});

/**
 * ADR-0004 §25: with a source-map upload token the plugin also creates and finalizes the release, and by default
 * runs `sentry release set-commits`, which sends the repository and commit IDs of the build (and, with a
 * repository integration in Sentry, lets Sentry read the commits' authors). Commit association is opt-in.
 */
describe("the release the build creates in Sentry (ADR-0004 §25)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ["without a source-map upload token", undefined],
    ["with a source-map upload token", "CANARYtoken0123456789"],
  ])("does not associate commits with it %s", async (_case, token) => {
    vi.stubEnv("SENTRY_AUTH_TOKEN", token ?? "");
    vi.resetModules();
    withSentryConfig.mockClear();
    await configFor(serverEnv);

    const options: { release?: { setCommits?: unknown } } | undefined = withSentryConfig.mock.calls[0]?.[1];
    expect(
      options?.release?.setCommits,
      "the plugin's default is { auto: true }: set it to false, or say what is sent in docs/data-inventory.md",
    ).toBe(false);
  });
});
