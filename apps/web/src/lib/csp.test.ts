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

/** Every directive of a policy with its tokens, in the order the policy lists them. */
function policy(csp: string): Record<string, string[]> {
  return Object.fromEntries(
    csp.split("; ").map((entry) => {
      const [name = "", ...tokens] = entry.split(" ");
      return [name, tokens];
    }),
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
  // sign-up without a visible error (the only sign is a console violation). Its list also names
  // https://clerk-telemetry.com: the template sends no SDK telemetry, so that host is left out (below).
  it.each([
    ["script-src", [FAPI, "https://challenges.cloudflare.com", "https://*.protect.clerk.com"]],
    // The port wildcard is required: Clerk Protect hosts are served on ports other than 443.
    ["connect-src", [FAPI, "https://*.protect.clerk.com:*"]],
    ["frame-src", ["https://challenges.cloudflare.com", "https://*.protect.clerk.com"]],
    ["img-src", ["https://img.clerk.com"]],
    ["style-src", ["'unsafe-inline'"]],
    ["worker-src", ["'self'", "blob:"]],
  ])("meets Clerk's required %s sources", (name, required) => {
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    for (const source of required) expect(directive(csp, name), source).toContain(source);
  });

  // The template turns Clerk's SDK telemetry off (layout.test.tsx, next-config.test.ts), so nothing sends to
  // Clerk's telemetry host and the policy must not allow it (ADR-0004 §29).
  it.each([false, true])("allows no host of Clerk's SDK telemetry (isDev: %s)", (isDev) => {
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev });
    expect(
      csp,
      "clerk-telemetry.com is allowed: the template turns Clerk's SDK telemetry off, so add the host back only together with ADR-0004 §29",
    ).not.toMatch(/clerk-telemetry/i);
  });

  it("allows nothing beyond what Clerk requires for framing", () => {
    const csp = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    // frame-ancestors 'none' and X-Frame-Options: DENY already forbid framing our own pages.
    expect(directive(csp, "frame-src")).not.toContain("'self'");
    expect(directive(csp, "frame-ancestors")).toEqual(["'none'"]);
    // Only connect-src takes the port wildcard.
    expect(directive(csp, "script-src").some((source) => source.includes(":*"))).toBe(false);
  });

  // The whole policy, token by token: a source added to any directive, or a directive removed, fails here.
  // `https:` or `*` in script-src, connect-src, img-src, frame-src, default-src or style-src would let the
  // page load from or send to any host, and a missing form-action or base-uri leaves forms and relative URLs open.
  describe("is exactly this policy", () => {
    const EU = {
      posthogHost: "https://eu.i.posthog.com",
      sentryDsn: "https://public@o123.ingest.de.sentry.io/456",
    };
    const PRODUCTION = {
      "default-src": ["'self'"],
      "script-src": [
        "'self'",
        "'unsafe-inline'",
        FAPI,
        "https://challenges.cloudflare.com",
        "https://*.protect.clerk.com",
      ],
      "connect-src": ["'self'", FAPI, "https://*.protect.clerk.com:*"],
      "img-src": ["'self'", "data:", "https://img.clerk.com"],
      "style-src": ["'self'", "'unsafe-inline'"],
      "font-src": ["'self'", "data:"],
      "frame-src": ["https://challenges.cloudflare.com", "https://*.protect.clerk.com"],
      "worker-src": ["'self'", "blob:"],
      "form-action": ["'self'"],
      "base-uri": ["'self'"],
      "object-src": ["'none'"],
      "frame-ancestors": ["'none'"],
      "upgrade-insecure-requests": [],
    };
    // The browser is allowed to stay on http while developing, so nothing is upgraded.
    const DEVELOPMENT = Object.fromEntries(
      Object.entries(PRODUCTION).filter(([name]) => name !== "upgrade-insecure-requests"),
    );

    it.each([
      ["in production without telemetry", { isDev: false }, PRODUCTION],
      [
        "in production with PostHog and Sentry",
        { isDev: false, ...EU },
        {
          ...PRODUCTION,
          "script-src": [
            "'self'",
            "'unsafe-inline'",
            FAPI,
            "https://challenges.cloudflare.com",
            "https://*.protect.clerk.com",
            "https://eu-assets.i.posthog.com",
          ],
          "connect-src": [
            "'self'",
            FAPI,
            "https://*.protect.clerk.com:*",
            "https://eu.i.posthog.com",
            "https://eu-assets.i.posthog.com",
            "https://o123.ingest.de.sentry.io",
          ],
        },
      ],
      [
        "in development without telemetry",
        { isDev: true },
        {
          ...DEVELOPMENT,
          "script-src": [
            "'self'",
            "'unsafe-inline'",
            "'unsafe-eval'",
            FAPI,
            "https://challenges.cloudflare.com",
            "https://*.protect.clerk.com",
          ],
          "connect-src": ["'self'", FAPI, "https://*.protect.clerk.com:*", "ws:"],
        },
      ],
      [
        "in development with PostHog and Sentry",
        { isDev: true, ...EU },
        {
          ...DEVELOPMENT,
          "script-src": [
            "'self'",
            "'unsafe-inline'",
            "'unsafe-eval'",
            FAPI,
            "https://challenges.cloudflare.com",
            "https://*.protect.clerk.com",
            "https://eu-assets.i.posthog.com",
          ],
          "connect-src": [
            "'self'",
            FAPI,
            "https://*.protect.clerk.com:*",
            "https://eu.i.posthog.com",
            "https://eu-assets.i.posthog.com",
            "https://o123.ingest.de.sentry.io",
            "ws:",
          ],
        },
      ],
    ])("%s", (_case, input, expected) => {
      expect(
        policy(buildContentSecurityPolicy({ clerkPublishableKey: key, ...input })),
        "the policy is not the one pinned here: a source was added to or dropped from a directive, or a directive was added or removed",
      ).toEqual(expected);
    });
  });

  it("keeps inline scripts (no nonce setup) and allows eval only in development", () => {
    const production = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: false });
    const development = buildContentSecurityPolicy({ clerkPublishableKey: key, isDev: true });
    expect(directive(production, "script-src")).toContain("'unsafe-inline'");
    expect(directive(production, "script-src")).not.toContain("'unsafe-eval'");
    expect(directive(development, "script-src")).toContain("'unsafe-eval'");
  });
});
