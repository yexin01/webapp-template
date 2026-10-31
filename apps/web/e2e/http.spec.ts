import { expect, test } from "@playwright/test";

/** Request-level checks that need no external accounts (mocked integrations). */

/**
 * The redirect a signed-out visitor gets: this app's own sign-in page, and back to `path` afterwards. Without
 * `signInUrl` (src/proxy.ts) Clerk sends the visitor to its hosted Account Portal, which has the same path on
 * another host, so the host is part of the check.
 */
function expectSignInRedirect(location: string | undefined, baseURL: string | undefined, path: string) {
  const app = new URL(baseURL ?? "");
  const target = new URL(location ?? "", app);
  expect(target.origin).toBe(app.origin);
  expect(target.pathname).toBe("/sign-in");
  const returnTo = new URL(target.searchParams.get("redirect_url") ?? "", app);
  expect(returnTo.origin).toBe(app.origin);
  expect(returnTo.pathname).toBe(path);
}

for (const path of ["/dashboard", "/billing", "/pro", "/billing/success", "/integrations"]) {
  test(`protected page ${path} redirects signed-out visitors to sign-in`, async ({ request, baseURL }) => {
    const response = await request.get(path, { maxRedirects: 0, headers: { Accept: "application/json" } });
    expect(response.status()).toBe(307);
    expectSignInRedirect(response.headers().location, baseURL, path);
  });
}

// Default deny (src/lib/page-access.ts): a page is public only when listed there. The proxy cannot tell
// a page from a typo, so any other path asks a signed-out visitor to sign in.
test("a page that is not listed as public requires sign-in", async ({ request, baseURL }) => {
  const response = await request.get("/definitely-not-a-page", {
    maxRedirects: 0,
    headers: { Accept: "application/json" },
  });
  expect(response.status()).toBe(307);
  expectSignInRedirect(response.headers().location, baseURL, "/definitely-not-a-page");
});

for (const path of ["/", "/sign-in", "/sign-up"]) {
  test(`public page ${path} opens without signing in`, async ({ request }) => {
    const response = await request.get(path, { maxRedirects: 0, headers: { Accept: "application/json" } });
    expect(response.status()).toBe(200);
  });
}

// SEC-02, on the production build this suite serves. The policy's exact content is csp.test.ts and the header set
// is next-config.test.ts; this proves they reach a response.
test("security headers are set", async ({ request }) => {
  const response = await request.get("/", { headers: { Accept: "application/json" } });
  const headers = response.headers();
  const policy = headers["content-security-policy"] ?? "";
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).toContain("default-src 'self'");
  expect(policy).toContain("upgrade-insecure-requests");
  expect(policy).not.toContain("'unsafe-eval'");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(headers["permissions-policy"]).toBe("camera=(), microphone=(), geolocation=()");
  // For this host only: includeSubDomains would pin plain-HTTP sibling subdomains to HTTPS (ADR-0004 §24).
  expect(headers["strict-transport-security"]).toBe("max-age=63072000");
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("API health is reachable same-origin and protected routes require auth", async ({ request }) => {
  const health = await request.get("/api/v1/health");
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({ status: "ok" });

  const me = await request.get("/api/v1/me");
  expect(me.status()).toBe(401);
  expect(me.headers()["content-type"]).toContain("application/problem+json");
  expect((await me.json()).code).toBe("UNAUTHENTICATED");
});

test("integration diagnostics require authentication", async ({ request }) => {
  const report = await request.get("/api/v1/diagnostics/integrations");
  expect(report.status()).toBe(401);
  const check = await request.post("/api/v1/diagnostics/integrations/database/check");
  expect(check.status()).toBe(401);
  const access = await request.get("/api/v1/diagnostics/access");
  expect(access.status()).toBe(401);
});

// API-04 with a valid token: test_api_security.py::test_api04_a_valid_token_in_the_session_cookie_is_refused
test("a forged session cookie does not authenticate API requests", async ({ request }) => {
  const me = await request.get("/api/v1/me", { headers: { Cookie: "__session=forged" } });
  expect(me.status()).toBe(401);
});

test("API errors use the problem-details contract through the web origin", async ({ request }) => {
  const response = await request.get("/api/v1/does-not-exist");
  expect(response.status()).toBe(404);
  const body = await response.json();
  expect(body).toMatchObject({ status: 404, code: "NOT_FOUND" });
  expect(typeof body.request_id).toBe("string");
});

// The proxy (src/proxy.ts) does not run on these paths, so nothing the root
// layout renders may need it: a missing file is a 404, never a server error.
for (const path of ["/favicon.ico", "/missing.png", "/api/v1x", "/_nextx", "/static/app.js"]) {
  test(`unmatched path ${path} is a 404, not a server error`, async ({ request }) => {
    const response = await request.get(path);
    expect(response.status()).toBe(404);
  });
}

test("the app icon is served and linked from pages", async ({ request }) => {
  const icon = await request.get("/icon.svg");
  expect(icon.status()).toBe(200);
  expect(icon.headers()["content-type"]).toContain("image/svg+xml");
  const page = await request.get("/", { headers: { Accept: "application/json" } });
  expect(await page.text()).toMatch(/<link rel="icon" href="\/icon\.svg\?[^"]*"/);
});
