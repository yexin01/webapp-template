// @vitest-environment node
import { AsyncLocalStorage } from "node:async_hooks";

import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest, type NextFetchEvent } from "next/server";

// Synthetic, well-formed keys; neither grants access to anything.
const publishableKey = `pk_live_${Buffer.from("clerk.example.com$").toString("base64")}`;
const secretKey = `sk_live_${"x".repeat(24)}`;

beforeAll(() => {
  // Next.js's request-scoped storage is a global in its runtime, but not in Node.
  vi.stubGlobal("AsyncLocalStorage", AsyncLocalStorage);
});
afterAll(() => vi.unstubAllGlobals());

async function loadProxy() {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", publishableKey);
  vi.stubEnv("CLERK_SECRET_KEY", secretKey);
  return import("./proxy");
}

describe("proxy matcher", () => {
  it.each([
    ["/", true],
    ["/dashboard", true],
    ["/notes", true],
    ["/billing/success", true],
    ["/sign-in/factor-one", true],
    // Clerk's Frontend API proxy and the clerk-js files it serves (the auto-proxy of a production
    // key on a *.vercel.app host): Clerk's default matcher runs for these, although they end in .js.
    ["/__clerk/v1/client", true],
    ["/__clerk/npm/@clerk/clerk-js@6/dist/clerk.browser.js", true],
    ["/__clerk/npm/@clerk/ui@1/dist/ui.browser.js", true],
    // The Python API, Next.js internals, and static files never reach the proxy.
    ["/api/v1/webhooks/stripe", false],
    ["/api/v1/me", false],
    ["/_next/static/chunks/a.js", false],
    ["/_next/image", false],
    ["/icon.svg", false],
    ["/favicon.ico", false],
    ["/robots.txt", false],
    ["/sitemap.xml", false],
  ])("for %s runs the proxy: %s", async (pathname, runs) => {
    const { config } = await loadProxy();
    expect(unstable_doesMiddlewareMatch({ config, url: `http://localhost:3000${pathname}` })).toBe(runs);
  });
});

describe("proxy page access", () => {
  const document = { "sec-fetch-dest": "document", accept: "text/html" };
  const event = {} as unknown as NextFetchEvent;

  async function visit(pathname: string) {
    const { default: proxy } = await loadProxy();
    const response = await proxy(
      new NextRequest(`http://localhost:3000${pathname}`, { headers: document }),
      event,
    );
    if (!response) throw new Error("the proxy returned no response");
    return response;
  }

  it.each(["/", "/sign-in", "/sign-in/factor-one", "/sign-up", "/sign-up/verify-email-address"])(
    "lets a signed-out visitor open the public page %s",
    async (pathname) => {
      const response = await visit(pathname);
      expect(response.status).toBe(200);
      expect(response.headers.get("location")).toBeNull();
    },
  );

  it.each(["/dashboard", "/billing", "/billing/success", "/pro", "/integrations", "/notes", "/notes/1"])(
    "sends a signed-out visitor from %s to sign-in",
    async (pathname) => {
      const response = await visit(pathname);
      expect(response.status).toBe(307);
      const location = new URL(response.headers.get("location") ?? "");
      expect(location.pathname).toBe("/sign-in");
      expect(location.searchParams.get("redirect_url")).toContain(pathname);
    },
  );
});
