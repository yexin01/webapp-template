import { expect, test } from "@playwright/test";

/** Request-level checks that need no external accounts (mocked integrations). */

for (const path of ["/dashboard", "/billing", "/pro", "/billing/success"]) {
  test(`protected page ${path} redirects signed-out visitors to sign-in`, async ({ request }) => {
    const response = await request.get(path, { maxRedirects: 0, headers: { Accept: "application/json" } });
    expect(response.status()).toBe(307);
    expect(response.headers().location).toMatch(/^(https?:\/\/[^/]+)?\/sign-in\?redirect_url=/);
  });
}

test("security headers are set", async ({ request }) => {
  const response = await request.get("/", { headers: { Accept: "application/json" } });
  const headers = response.headers();
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
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

test("session cookies are not accepted for API authentication", async ({ request }) => {
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
