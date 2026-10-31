// @vitest-environment node
import { AsyncLocalStorage } from "node:async_hooks";
import { createSign, generateKeyPairSync } from "node:crypto";

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

/**
 * What next.config.ts passes down: `parties`, the origins a session token may have been issued for, and
 * `jwtKey`, the instance's PEM public key.
 */
async function loadProxy(parties?: string, jwtKey?: string) {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", publishableKey);
  vi.stubEnv("CLERK_SECRET_KEY", secretKey);
  vi.stubEnv("NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES", parties);
  vi.stubEnv("NEXT_PUBLIC_CLERK_JWT_KEY", jwtKey);
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
      // The app's own sign-in page: without `signInUrl` Clerk sends the visitor to its hosted Account Portal,
      // which has the same path on another host.
      expect(location.origin).toBe("http://localhost:3000");
      expect(location.pathname).toBe("/sign-in");
      expect(location.searchParams.get("redirect_url")).toContain(pathname);
    },
  );
});

// Real Clerk verification, with throwaway key pairs: the proxy gets the signing key (from Clerk's backend API, stubbed
// below, or from CLERK_JWT_KEY), checks the signature, and then the token's azp claim against the parties.
const keys = generateKeyPairSync("rsa", { modulusLength: 2048 });
type KeyPair = typeof keys;
const event = {} as unknown as NextFetchEvent;

const publicPem = (pair: KeyPair) => pair.publicKey.export({ type: "spki", format: "pem" }).toString();
const keySet = (pair: KeyPair, kid: string) => ({
  keys: [{ ...pair.publicKey.export({ format: "jwk" }), kid, alg: "RS256", use: "sig" }],
});

/** A session token as Clerk issues it, signed by `pair` as key `kid`; `azp` is the page it was issued to. */
function sessionToken(azp: string, pair: KeyPair = keys, kid = "ins_test") {
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const unsigned = [
    encode({ alg: "RS256", typ: "JWT", kid }),
    encode({ sub: "user_a", sid: "sess_a", azp, iat: now - 5, nbf: now - 5, exp: now + 300 }),
  ].join(".");
  return `${unsigned}.${createSign("RSA-SHA256").update(unsigned).sign(pair.privateKey).toString("base64url")}`;
}

/** A signed-in visitor's document request for a protected page, with `token`. */
async function open(proxy: Awaited<ReturnType<typeof loadProxy>>["default"], token: string) {
  const request = new NextRequest("https://app.example.com/dashboard", {
    headers: { "sec-fetch-dest": "document", accept: "text/html", authorization: `Bearer ${token}` },
  });
  const response = await proxy(request, event);
  if (!response) throw new Error("the proxy returned no response");
  return response;
}

describe("proxy authorized parties", () => {
  beforeAll(() => {
    vi.stubGlobal("fetch", async () => Response.json(keySet(keys, "ins_test")));
  });

  const LISTED = ["https://app.example.com", "https://preview.example.com"];

  it("opens a protected page for a session issued to any listed page", { timeout: 30_000 }, async () => {
    const { default: proxy } = await loadProxy(LISTED.join(","));
    for (const azp of LISTED) {
      const response = await open(proxy, sessionToken(azp));
      expect(response.status, azp).toBe(200);
      expect(response.headers.get("location"), azp).toBeNull();
    }
  });

  it("treats a session issued to any other page as signed out", { timeout: 30_000 }, async () => {
    const { default: proxy } = await loadProxy(LISTED.join(","));
    for (const azp of [
      "https://evil.example.com",
      "https://app.example.com.evil.example.com",
      "http://app.example.com",
      "https://app.example.com:8443",
      "https://App.example.com",
      "",
    ]) {
      const response = await open(proxy, sessionToken(azp));
      expect(response.status, azp).toBe(307);
      const location = new URL(response.headers.get("location") ?? "");
      expect(location.origin, azp).toBe("https://app.example.com");
      expect(location.pathname, azp).toBe("/sign-in");
    }
  });

  it(
    "checks no origin where next.config.ts named none (automated tests, which have no Clerk instance)",
    { timeout: 30_000 },
    async () => {
      const { default: proxy } = await loadProxy("");
      expect((await open(proxy, sessionToken("https://evil.example.com"))).status).toBe(200);
    },
  );
});

describe("proxy signing key", () => {
  const PAGE = "https://app.example.com";
  const other = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const fetchKeySet = vi.fn<(input: unknown) => Promise<Response>>();

  beforeEach(() => {
    fetchKeySet.mockReset();
    vi.stubGlobal("fetch", fetchKeySet);
  });

  it(
    "verifies a session token with CLERK_JWT_KEY, and fetches no key set from Clerk",
    { timeout: 30_000 },
    async () => {
      fetchKeySet.mockRejectedValue(new Error("the proxy asked Clerk for its key set"));
      const { default: proxy } = await loadProxy(PAGE, publicPem(keys));
      // A key ID that no earlier test fetched a key set for, or Clerk's SDK would have it cached.
      const response = await open(proxy, sessionToken(PAGE, keys, "ins_not_cached"));
      expect(response.status).toBe(200);
      expect(response.headers.get("location")).toBeNull();
      expect(fetchKeySet).not.toHaveBeenCalled();
    },
  );

  it(
    "trusts that key alone: a token from another key is refused although Clerk's key set holds it",
    { timeout: 30_000 },
    async () => {
      fetchKeySet.mockImplementation(async () => Response.json(keySet(other, "ins_other")));
      const { default: proxy } = await loadProxy(PAGE, publicPem(keys));
      const response = await open(proxy, sessionToken(PAGE, other, "ins_other"));
      expect(response.status).toBe(307);
      const location = new URL(response.headers.get("location") ?? "");
      expect(location.origin).toBe(PAGE);
      expect(location.pathname).toBe("/sign-in");
      expect(fetchKeySet).not.toHaveBeenCalled();
    },
  );

  it("without it, still fetches Clerk's key set and verifies with that", { timeout: 30_000 }, async () => {
    for (const [label, jwtKey] of [
      ["unset", undefined],
      ["empty", ""],
    ] as const) {
      // A key ID of its own, so that no key set cached by an earlier case answers for it.
      const kid = `ins_fetched_${label}`;
      fetchKeySet.mockReset();
      fetchKeySet.mockImplementation(async () => Response.json(keySet(other, kid)));
      const { default: proxy } = await loadProxy(PAGE, jwtKey);
      const response = await open(proxy, sessionToken(PAGE, other, kid));
      expect(fetchKeySet, label).toHaveBeenCalled();
      expect(response.status, label).toBe(200);
    }
  });
});
