import { createApiClient, isAllowedRedirect } from "./client";
import { ApiError, presentError } from "./errors";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the bearer token, never cookies, to the same-origin API", async () => {
    const fetchMock = mockFetch(200, { user_id: "u1" });
    await createApiClient(async () => "tok_123").me();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/me");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok_123");
    expect(init.credentials).toBe("omit");
  });

  it("scopes organization requests and encodes identifiers", async () => {
    const fetchMock = mockFetch(200, { items: [], next_cursor: null });
    await createApiClient(async () => null).listProjects("org_a/b", "c u");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/orgs/org_a%2Fb/projects?limit=20&cursor=c%20u");
  });

  it("sends an idempotency key on checkout", async () => {
    const fetchMock = mockFetch(200, { url: "https://checkout.stripe.com/x" });
    await createApiClient(async () => "t").startCheckout("pro_yearly", "key-12345678");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-12345678");
    expect(init.body).toBe(JSON.stringify({ plan: "pro_yearly" }));
  });

  it("normalizes problem responses", async () => {
    mockFetch(403, {
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      code: "ENTITLEMENT_REQUIRED",
      detail: "This feature requires the Pro plan.",
      request_id: "req1",
    });
    const error = await createApiClient(async () => "t")
      .proInsight(null)
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("ENTITLEMENT_REQUIRED");
    expect((error as ApiError).requestId).toBe("req1");
    expect(presentError(error).action).toBe("upgrade");
  });

  it("maps network failures and unknown errors to safe messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("boom")));
    const error = await createApiClient(async () => "t")
      .me()
      .catch((e: unknown) => e);
    expect((error as ApiError).code).toBe("NETWORK_ERROR");
    expect(presentError(new Error("internal detail")).message).not.toContain("internal detail");
    expect(presentError(new ApiError(401, "UNAUTHENTICATED", "x", null)).action).toBe("sign-in");
  });
});

describe("redirect allowlist", () => {
  const origin = "http://localhost:3000";
  it.each([
    ["https://checkout.stripe.com/c/pay/cs_test_1", true],
    ["https://billing.stripe.com/p/session/1", true],
    ["/billing", true],
    ["http://checkout.stripe.com/x", false],
    ["https://stripe.com.evil.example/x", false],
    ["https://evil.example/?https://checkout.stripe.com", false],
    ["javascript:alert(1)", false],
  ])("%s -> %s", (url, allowed) => {
    expect(isAllowedRedirect(url, origin)).toBe(allowed);
  });
});
