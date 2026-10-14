import { expectTypeOf } from "vitest";

import {
  createApiClient,
  isAllowedRedirect,
  type ApiClient,
  type ApiFetchClient,
  type Schemas,
} from "./client";
import { ApiError, presentError } from "./errors";

/**
 * jsdom has no fetch, so tests use Node's Request, which rejects the relative
 * URLs that browsers resolve against the document. This subclass resolves them
 * the way a browser does and records the URL exactly as the client built it.
 */
let requestedUrls: string[] = [];
class BrowserRequest extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (typeof input === "string") requestedUrls.push(input);
    super(typeof input === "string" ? new URL(input, window.location.href) : input, init);
  }
}

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn<(request: Request) => Promise<Response>>().mockImplementation(
    async () =>
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

function sentRequest(fetchMock: ReturnType<typeof mockFetch>, index = 0): Request {
  const request = fetchMock.mock.calls[index]?.[0];
  if (!(request instanceof Request)) throw new Error(`fetch call ${index} did not receive a Request`);
  return request;
}

beforeEach(() => {
  requestedUrls = [];
  vi.stubGlobal("Request", BrowserRequest);
});
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the bearer token, never cookies, to the same-origin API", async () => {
    const fetchMock = mockFetch(200, { user_id: "u1" });
    const me = await createApiClient(async () => "tok_123").me();
    const request = sentRequest(fetchMock);
    expect(requestedUrls[0]).toBe("/api/v1/me");
    expect(new URL(request.url).origin).toBe(window.location.origin);
    expect(request.method).toBe("GET");
    expect(request.headers.get("Authorization")).toBe("Bearer tok_123");
    expect(request.headers.get("Accept")).toBe("application/json");
    expect(request.headers.get("Cookie")).toBeNull();
    expect(request.headers.get("Content-Type")).toBeNull();
    expect(request.credentials).toBe("omit");
    expect(request.cache).toBe("no-store");
    expect(me).toEqual({ user_id: "u1" });
  });

  it("omits the Authorization header when there is no session token", async () => {
    const fetchMock = mockFetch(200, { user_id: "u1" });
    await createApiClient(async () => null).me();
    expect(sentRequest(fetchMock).headers.get("Authorization")).toBeNull();
    expect(sentRequest(fetchMock).credentials).toBe("omit");
  });

  it("scopes organization requests and encodes identifiers", async () => {
    mockFetch(200, { items: [], next_cursor: null });
    await createApiClient(async () => null).listProjects("org_a/b", "c u");
    expect(requestedUrls[0]).toBe("/api/v1/orgs/org_a%2Fb/projects?limit=20&cursor=c%20u");
  });

  it("uses personal paths without an organization and omits an empty cursor", async () => {
    mockFetch(200, { items: [], next_cursor: null });
    const api = createApiClient(async () => null);
    await api.listProjects(null);
    await api.listProjects(null, null);
    await api.entitlement(null);
    await api.proInsight(null);
    expect(requestedUrls).toEqual([
      "/api/v1/projects?limit=20",
      "/api/v1/projects?limit=20",
      "/api/v1/entitlement",
      "/api/v1/pro/insight",
    ]);
  });

  it("deletes with encoded path parameters and handles 204", async () => {
    const fetchMock = vi
      .fn<(request: Request) => Promise<Response>>()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await createApiClient(async () => "t").deleteProject("org_1", "p/1");
    expect(result).toBeUndefined();
    expect(requestedUrls[0]).toBe("/api/v1/orgs/org_1/projects/p%2F1");
    expect(sentRequest(fetchMock).method).toBe("DELETE");
  });

  it("sends an idempotency key on checkout", async () => {
    const fetchMock = mockFetch(200, { url: "https://checkout.stripe.com/x" });
    await createApiClient(async () => "t").startCheckout("pro_yearly", "key-12345678");
    const request = sentRequest(fetchMock);
    expect(requestedUrls[0]).toBe("/api/v1/billing/checkout");
    expect(request.method).toBe("POST");
    expect(request.headers.get("Idempotency-Key")).toBe("key-12345678");
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(await request.text()).toBe(JSON.stringify({ plan: "pro_yearly" }));
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
    expect((error as ApiError).status).toBe(403);
    expect((error as ApiError).code).toBe("ENTITLEMENT_REQUIRED");
    expect((error as ApiError).message).toBe("This feature requires the Pro plan.");
    expect((error as ApiError).requestId).toBe("req1");
    expect(presentError(error).action).toBe("upgrade");
  });

  it.each([
    ["a non-JSON body", "Bad gateway"],
    ["a JSON body that is not a problem", JSON.stringify({ detail: "upstream said no" })],
  ])("maps an error with %s to HTTP_ERROR without echoing it", async (_label, body) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 502 })));
    const error = await createApiClient(async () => "t")
      .billingStatus()
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).code).toBe("HTTP_ERROR");
    expect((error as ApiError).requestId).toBeNull();
    expect((error as ApiError).message).not.toContain("upstream");
    expect((error as ApiError).message).not.toContain("Bad gateway");
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

  it("re-throws an aborted request untouched", async () => {
    const abort = new DOMException("The operation was aborted.", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(createApiClient(async () => "t").me()).rejects.toBe(abort);
  });
});

describe("compile-time contract (enforced by `pnpm typecheck`)", () => {
  it("returns the contract's response types", () => {
    type Result<K extends keyof ApiClient> = Awaited<ReturnType<ApiClient[K]>>;
    expectTypeOf<Result<"me">>().toEqualTypeOf<Schemas["Me"]>();
    expectTypeOf<Result<"entitlement">>().toEqualTypeOf<Schemas["Entitlement"]>();
    expectTypeOf<Result<"listProjects">>().toEqualTypeOf<Schemas["ProjectPage"]>();
    expectTypeOf<Result<"createProject">>().toEqualTypeOf<Schemas["Project"]>();
    expectTypeOf<Result<"deleteProject">>().toEqualTypeOf<void>();
    expectTypeOf<Result<"billingStatus">>().toEqualTypeOf<Schemas["BillingStatus"]>();
    expectTypeOf<Result<"startCheckout">>().toEqualTypeOf<Schemas["Redirect"]>();
    expectTypeOf<Result<"openPortal">>().toEqualTypeOf<Schemas["Redirect"]>();
    expectTypeOf<Result<"proInsight">>().toEqualTypeOf<Schemas["ProInsight"]>();
    expectTypeOf<Result<"llmCheck">>().toEqualTypeOf<Schemas["LLMCheck"]>();
    expectTypeOf<Result<"integrations">>().toEqualTypeOf<Schemas["IntegrationsReport"]>();
    expectTypeOf<Result<"runIntegrationCheck">>().toEqualTypeOf<Schemas["IntegrationCheck"]>();
  });

  it("rejects unknown paths, wrong parameters, and invalid bodies", () => {
    // Never invoked: each line must fail to compile, or `tsc` reports an unused directive.
    const misuse = (client: ApiFetchClient) => {
      // @ts-expect-error -- misspelled path
      void client.GET("/api/v1/mee");
      // @ts-expect-error -- paths carry the /api/v1 prefix
      void client.GET("/me");
      // @ts-expect-error -- method not defined for this path
      void client.DELETE("/api/v1/me");
      // @ts-expect-error -- required path parameter missing
      void client.GET("/api/v1/orgs/{org_id}/entitlement");
      // @ts-expect-error -- misspelled path parameter
      void client.GET("/api/v1/orgs/{org_id}/entitlement", { params: { path: { orgId: "o" } } });
      // @ts-expect-error -- misspelled query parameter
      void client.GET("/api/v1/projects", { params: { query: { limt: 20 } } });
      // @ts-expect-error -- query parameter of the wrong type
      void client.GET("/api/v1/projects", { params: { query: { limit: "20" } } });
      void client.POST("/api/v1/billing/checkout", {
        params: { header: { "Idempotency-Key": "k" } },
        // @ts-expect-error -- plan outside the contract's enum
        body: { plan: "pro_weekly" },
      });
      // @ts-expect-error -- required Idempotency-Key header missing
      void client.POST("/api/v1/billing/checkout", { body: { plan: "pro_monthly" } });
      // @ts-expect-error -- request body missing
      void client.POST("/api/v1/projects", {});
      void client.GET("/api/v1/me").then(({ data }) => {
        // @ts-expect-error -- field not in the response schema
        void data?.email;
      });
    };
    expect(misuse).toBeTypeOf("function");
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
