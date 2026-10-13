import { buildContentSecurityPolicy, clerkFrontendApiOrigin } from "./csp";

const key = `pk_test_${btoa("example-app.clerk.accounts.dev$")}`;

describe("content security policy", () => {
  it("derives the Clerk Frontend API origin from the publishable key", () => {
    expect(clerkFrontendApiOrigin(key)).toBe("https://example-app.clerk.accounts.dev");
    expect(clerkFrontendApiOrigin("pk_test_%%%")).toBeNull();
    expect(clerkFrontendApiOrigin(`pk_test_${btoa("evil.example/path$")}`)).toBeNull();
  });

  it("allows only configured origins and forbids framing and plugins", () => {
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("https://example-app.clerk.accounts.dev");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).not.toContain("posthog");
    expect(csp).toContain("upgrade-insecure-requests");
  });

  it("adds telemetry origins only when enabled", () => {
    const csp = buildContentSecurityPolicy({
      clerkPublishableKey: key,
      posthogHost: "https://eu.i.posthog.com",
      sentryDsn: "https://public@o123.ingest.de.sentry.io/456",
      isDev: true,
    });
    expect(csp).toContain("https://eu.i.posthog.com");
    expect(csp).toContain("https://eu-assets.i.posthog.com");
    expect(csp).toContain("https://o123.ingest.de.sentry.io");
    expect(csp).toContain("'unsafe-eval'");
  });
});
