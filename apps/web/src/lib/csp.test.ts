import { buildContentSecurityPolicy, clerkFrontendApiOrigin, posthogOrigins } from "./csp";

const key = `pk_test_${btoa("example-app.clerk.accounts.dev$")}`;
const FAPI = "https://example-app.clerk.accounts.dev";

/** The tokens of one directive, exactly as the browser splits them. */
function directive(csp: string, name: string): string[] {
  return (
    csp
      .split("; ")
      .find((d) => d.startsWith(`${name} `))
      ?.split(" ")
      .slice(1) ?? []
  );
}

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
    for (const origin of connect) expect(directive(csp, "connect-src")).toContain(origin);
    for (const origin of script) expect(directive(csp, "script-src")).toContain(origin);
    // Nothing derived by rewriting the host name, such as https://us-assets.posthog.com.
    expect(csp).not.toMatch(/https:\/\/[a-z]+-assets\.posthog\.com/);
    expect(csp).not.toContain("ph-assets.example.com");
  });

  // Clerk's required sources (https://clerk.com/docs/guides/secure/best-practices/csp-headers).
  // Recheck this table on every @clerk/nextjs upgrade: a source missing here breaks sign-in or
  // sign-up without a visible error (the only sign is a console violation).
  it.each([
    ["script-src", [FAPI, "https://challenges.cloudflare.com", "https://*.protect.clerk.com"]],
    // The port wildcard is required: Clerk Protect hosts are served on ports other than 443.
    ["connect-src", [FAPI, "https://*.protect.clerk.com:*", "https://clerk-telemetry.com"]],
    ["frame-src", ["https://challenges.cloudflare.com", "https://*.protect.clerk.com"]],
    ["img-src", ["https://img.clerk.com"]],
    ["style-src", ["'unsafe-inline'"]],
    ["worker-src", ["'self'", "blob:"]],
  ])("meets Clerk's required %s sources", (name, required) => {
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    for (const source of required) expect(directive(csp, name), source).toContain(source);
  });

  it("allows nothing beyond what Clerk requires for framing and telemetry", () => {
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    // Telemetry goes to the apex host only (and only from development instances).
    expect(directive(csp, "connect-src")).not.toContain("https://*.clerk-telemetry.com");
    // frame-ancestors 'none' and X-Frame-Options: DENY already forbid framing our own pages.
    expect(directive(csp, "frame-src")).not.toContain("'self'");
    expect(directive(csp, "frame-ancestors")).toEqual(["'none'"]);
    // Only connect-src takes the port wildcard.
    expect(directive(csp, "script-src").some((source) => source.includes(":*"))).toBe(false);
  });

  it("keeps inline scripts (no nonce setup) and allows eval only in development", () => {
    const production = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    const development = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: true });
    expect(directive(production, "script-src")).toContain("'unsafe-inline'");
    expect(directive(production, "script-src")).not.toContain("'unsafe-eval'");
    expect(directive(development, "script-src")).toContain("'unsafe-eval'");
  });
});
