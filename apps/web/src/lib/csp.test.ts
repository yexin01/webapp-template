import { buildContentSecurityPolicy, clerkFrontendApiOrigin, posthogOrigins } from "./csp";

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

  it.each([
    [
      "https://us.i.posthog.com",
      ["https://us.i.posthog.com", "https://us-assets.i.posthog.com"],
      ["https://us-assets.i.posthog.com"],
    ],
    [
      "https://eu.i.posthog.com/",
      ["https://eu.i.posthog.com", "https://eu-assets.i.posthog.com"],
      ["https://eu-assets.i.posthog.com"],
    ],
    // The app (UI) hosts: events go to the region's ingestion host, flags to the host itself.
    [
      "https://us.posthog.com",
      ["https://us.i.posthog.com", "https://us-assets.i.posthog.com", "https://us.posthog.com"],
      ["https://us-assets.i.posthog.com"],
    ],
    [
      "https://eu.posthog.com",
      ["https://eu.i.posthog.com", "https://eu-assets.i.posthog.com", "https://eu.posthog.com"],
      ["https://eu-assets.i.posthog.com"],
    ],
    [
      "https://app.posthog.com",
      ["https://us.i.posthog.com", "https://us-assets.i.posthog.com"],
      ["https://us-assets.i.posthog.com"],
    ],
    // A reverse proxy serves events and assets itself; a path is not part of the origin.
    ["https://ph.example.com/ingest", ["https://ph.example.com"], ["https://ph.example.com"]],
    ["https://e.example-assets.com", ["https://e.example-assets.com"], ["https://e.example-assets.com"]],
    ["not a url", [], []],
  ])("routes PostHog host %s as posthog-js does", (host, connect, script) => {
    expect(posthogOrigins(host)).toEqual({ connect, script });
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, posthogHost: host, isDev: false });
    const directive = (name: string) =>
      csp
        .split("; ")
        .find((d) => d.startsWith(`${name} `))
        ?.split(" ")
        .slice(1) ?? [];
    for (const origin of connect) expect(directive("connect-src")).toContain(origin);
    for (const origin of script) expect(directive("script-src")).toContain(origin);
    // Nothing derived by rewriting the host name, such as https://us-assets.posthog.com.
    expect(csp).not.toMatch(/https:\/\/[a-z]+-assets\.posthog\.com/);
    expect(csp).not.toContain("ph-assets.example.com");
  });
});
