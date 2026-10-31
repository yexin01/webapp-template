// @vitest-environment node
import { PHASE_PRODUCTION_BUILD } from "next/constants";

/**
 * What next.config.ts hands the rest of the app. The proxy may not read the server-only variables the
 * authorized parties and the Clerk key come from (ENV-07), so the config passes them down as NEXT_PUBLIC
 * values, which Next.js inlines into the proxy at build time. The validator and the files are replaced:
 * this is about the wiring, not the rules (env.server.test.ts, env-cases.test.ts).
 */
const loadServerEnv = vi.hoisted(() => vi.fn());
vi.mock("@sentry/nextjs/config", () => ({ withSentryConfig: (config: unknown) => config }));
vi.mock("./lib/env-files", () => ({ loadEnvFiles: () => [], strayEnvFiles: () => [] }));
vi.mock("./lib/env.server", () => ({
  ConfigError: class extends Error {},
  isWebVariable: () => false,
  loadServerEnv,
  sentryBuildOptions: () => ({}),
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
