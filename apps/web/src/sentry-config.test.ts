/**
 * OBS-02 / OBS-08: the browser and server Sentry configurations scrub what
 * they send. Synthetic canaries go through each SDK's beforeSend,
 * beforeBreadcrumb, and beforeSendSpan hooks, and the client hooks for the
 * trace context, exactly as configured, and must not come out.
 */
import type { Breadcrumb, ErrorEvent, init as sentryInit } from "@sentry/nextjs";

type SpanJSON = Parameters<NonNullable<NonNullable<Parameters<typeof sentryInit>[0]>["beforeSendSpan"]>>[0];

const init = vi.hoisted(() => vi.fn());
const captureRouterTransitionStart = vi.hoisted(() => vi.fn());
/** The client hooks each configuration registers, by name. */
const hooks = vi.hoisted(() => new Map<string, (value: unknown) => void>());
vi.mock("@sentry/nextjs", () => ({
  init,
  captureRouterTransitionStart,
  getClient: () => ({
    on: (hook: string, callback: (value: unknown) => void) => {
      hooks.set(hook, callback);
      return () => hooks.delete(hook);
    },
  }),
}));
vi.mock("./lib/env", () => ({
  publicEnv: { appEnv: "test", errorReporting: { enabled: true, dsn: "https://key@o1.ingest.sentry.io/1" } },
}));
vi.mock("./lib/env.server", () => ({
  loadServerEnv: () => ({
    appEnv: "test",
    errorReporting: { enabled: true, dsn: "https://key@o1.ingest.sentry.io/1", tracesSampleRate: 0 },
  }),
}));

const SECRET = "sk_" + "live_CANARY1234567890";
const EMAIL = "canary.person@example.com";
const TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJDQU5BUlkifQ.c2lnbmF0dXJlQ0FOQVJZ";
const TICKET = "CANARYTICKET42";
const CANARIES = [SECRET, EMAIL, TOKEN, TICKET, "CANARY"];

type Options = {
  beforeSend: (event: ErrorEvent, hint: unknown) => ErrorEvent | null;
  beforeBreadcrumb: (breadcrumb: Breadcrumb, hint?: unknown) => Breadcrumb | null;
  beforeSendSpan?: (span: SpanJSON) => SpanJSON;
};

async function optionsOf(module: string): Promise<Options> {
  init.mockClear();
  hooks.clear();
  vi.resetModules();
  await import(/* @vite-ignore */ module);
  expect(init).toHaveBeenCalledOnce();
  return init.mock.calls[0]?.[0] as Options;
}

const breadcrumbs = (): Breadcrumb[] => [
  {
    category: "console",
    level: "error",
    message: `Failed for ${EMAIL} with ${SECRET}`,
    data: {
      logger: "console",
      arguments: [
        `Failed for ${EMAIL}`,
        { auth: `Bearer ${TOKEN}`, nested: [{ key: SECRET }] },
        new Error(SECRET),
      ],
    },
  },
  { category: "navigation", data: { from: `/sign-up?__clerk_ticket=${TICKET}`, to: `/dashboard#${TICKET}` } },
  {
    category: "fetch",
    type: "http",
    data: { method: "GET", url: `/api/v1/me?token=${TICKET}`, status_code: 401 },
  },
];

const event = (): ErrorEvent =>
  ({
    type: undefined,
    message: `Checkout failed for ${EMAIL}`,
    logentry: { message: `Checkout failed for ${EMAIL}`, params: [SECRET] },
    exception: { values: [{ type: "Error", value: `Stripe said ${SECRET}` }] },
    extra: { payload: { email: EMAIL, token: TOKEN, list: [SECRET] } },
    user: { email: EMAIL, id: "user_1" },
    request: {
      url: `https://app.example.com/billing/success?session_id=${TICKET}#${TICKET}`,
      query_string: `session_id=${TICKET}`,
      headers: { Authorization: `Bearer ${TOKEN}` },
      cookies: { __session: TOKEN },
      data: { email: EMAIL },
    },
    breadcrumbs: breadcrumbs(),
  }) as ErrorEvent;

// A request span as Next.js and the SDK build it, with tokens in its URLs: an
// invitation ticket, a handshake token, and a Checkout session ID.
const span = (): SpanJSON => ({
  trace_id: "0123456789abcdef0123456789abcdef",
  span_id: "0123456789abcdef",
  start_timestamp: 1,
  end_timestamp: 2,
  status: "ok",
  is_segment: true,
  name: `GET /sign-up?__clerk_ticket=${TICKET}&__clerk_status=sign_up`,
  attributes: {
    "next.span_type": "BaseServer.handleRequest",
    "next.route": "/sign-up/[[...sign-up]]",
    "http.method": "GET",
    "http.status_code": 200,
    "http.target": `/sign-up?__clerk_ticket=${TICKET}&__clerk_status=sign_up`,
    "http.url": `https://app.example.com/billing/success?session_id=${TICKET}#${TICKET}`,
    "url.full": `https://app.example.com/dashboard?__clerk_handshake=${TOKEN}&__clerk_handshake_nonce=${TICKET}`,
    "url.query": `__clerk_db_jwt=${TOKEN}`,
    "url.fragment": TICKET,
    "sentry.segment.name": `GET /sign-up?__clerk_ticket=${TICKET}`,
    "sentry.status.message": `Checkout failed for ${EMAIL} with ${SECRET}`,
    "user.email": EMAIL,
    "browser.web_vital.lcp.urls": [`https://app.example.com/a?token=${TICKET}`],
    "next.rsc.url": { value: `/billing/success?_rsc=1&session_id=${TICKET}` },
  },
  links: [{ trace_id: "1".repeat(32), span_id: "1".repeat(16), attributes: { from: `/x?token=${TICKET}` } }],
});

function expectClean(value: unknown) {
  const json = JSON.stringify(value);
  for (const canary of CANARIES) expect(json).not.toContain(canary);
}

describe.each([
  ["browser", "./instrumentation-client"],
  ["server", "./sentry.server.config"],
])("%s Sentry configuration", (_name, module) => {
  it("scrubs breadcrumbs, including console arguments and navigation URLs", async () => {
    const { beforeBreadcrumb } = await optionsOf(module);
    const scrubbed = breadcrumbs().map((breadcrumb) => beforeBreadcrumb(breadcrumb));
    expectClean(scrubbed);
    expect(scrubbed[1]?.data).toEqual({ from: "/sign-up", to: "/dashboard" });
    expect(scrubbed[2]?.data).toMatchObject({ method: "GET", url: "/api/v1/me", status_code: 401 });
    expect(beforeBreadcrumb({ category: "ui.input", message: EMAIL })).toBeNull();
  });

  it("keeps element paths and attribute names in click breadcrumbs, never attribute values", async () => {
    const { beforeBreadcrumb, beforeSend } = await optionsOf(module);
    const path = "li.list-item > button.button.button-small";
    const click = (label: string): Breadcrumb => ({
      category: "ui.click",
      message: `${path}[aria-label="${label}"][type="button"]`,
    });
    expect(beforeBreadcrumb(click("Delete Jane CANARY Doe"))?.message).toBe(`${path}[aria-label][type]`);
    // Sentry copies values unescaped, so they can hold quotes, brackets, and separators.
    for (const label of [
      'Delete "Jane CANARY" Doe',
      'x"]Jane CANARY[a="',
      'x"] > div[title="Jane CANARY',
      // A value can forge attribute sections and elements, whose names would carry the text.
      'x"][Jane_CANARY_Doe="',
      'x"] > div.Jane_CANARY[title="Doe',
      'x"] > div.Jane_CANARY[aria-label="',
    ]) {
      const message = beforeBreadcrumb(click(label))?.message ?? "";
      expect(message).not.toContain("CANARY");
      expect(message.startsWith(path)).toBe(true);
    }
    expect(beforeBreadcrumb(click('x"][Jane_CANARY_Doe="'))?.message).toBe(path);
    expectClean(beforeSend({ type: undefined, breadcrumbs: [click("Delete Jane CANARY Doe")] }, {}));
  });

  it("scrubs the event message, extra data, exception, request, user, and breadcrumbs", async () => {
    const { beforeSend } = await optionsOf(module);
    const scrubbed = beforeSend(event(), {});
    expectClean(scrubbed);
    expect(scrubbed?.request?.url).toBe("https://app.example.com/billing/success");
    expect(scrubbed?.breadcrumbs).toHaveLength(3);
    expect(scrubbed?.message).toBe("Checkout failed for [REDACTED]");
  });

  it("strips query strings and fragments from span names and URL attributes, and redacts the rest", async () => {
    const { beforeSendSpan } = await optionsOf(module);
    expect(beforeSendSpan).toBeTypeOf("function");
    const scrubbed = beforeSendSpan?.(span());
    expectClean(scrubbed);
    expect(scrubbed?.name).toBe("GET /sign-up");
    expect(scrubbed?.attributes).toEqual({
      "next.span_type": "BaseServer.handleRequest",
      "next.route": "/sign-up/[[...sign-up]]",
      "http.method": "GET",
      "http.status_code": 200,
      "http.target": "/sign-up",
      "http.url": "https://app.example.com/billing/success",
      "url.full": "https://app.example.com/dashboard",
      "sentry.segment.name": "GET /sign-up",
      "sentry.status.message": "Checkout failed for [REDACTED] with [REDACTED]",
      "browser.web_vital.lcp.urls": ["https://app.example.com/a"],
      "next.rsc.url": { value: "/billing/success" },
    });
    expect(scrubbed?.links?.[0]?.attributes).toEqual({ from: "/x" });
  });

  it("scrubs the root span name in the trace context of envelope and baggage headers", async () => {
    await optionsOf(module);
    const createDsc = hooks.get("createDsc");
    const beforeEnvelope = hooks.get("beforeEnvelope");
    expect(createDsc).toBeTypeOf("function");
    expect(beforeEnvelope).toBeTypeOf("function");
    const context = (transaction: string) => ({ trace_id: "0".repeat(32), public_key: "key", transaction });

    // As the SDK creates it, for envelopes and the baggage header of outgoing requests.
    const created = context(`GET /invite/${EMAIL}?__clerk_ticket=${TICKET}#${TICKET}`);
    createDsc?.(created);
    expect(created).toEqual(context("GET /invite/[REDACTED]"));

    // In every envelope, including a trace context received from upstream.
    const envelope = [{ sent_at: "now", trace: context(`GET /billing/success?session_id=${TICKET}`) }, []];
    beforeEnvelope?.(envelope);
    expectClean(envelope);
    expect(envelope[0]).toEqual({ sent_at: "now", trace: context("GET /billing/success") });

    // An upstream caller sets every field of the trace context it sends, not only the name.
    const upstream = [
      {
        trace: {
          ...context("GET /dashboard"),
          release: `web@1 https://app.example.com/sign-up?__clerk_ticket=${TICKET}`,
          environment: EMAIL,
          sample_rate: "1",
          sampled: "true",
        },
      },
    ];
    beforeEnvelope?.(upstream);
    expectClean(upstream);
    expect(upstream[0]?.trace).toEqual({
      ...context("GET /dashboard"),
      release: "web@1 https://app.example.com/sign-up",
      environment: "[REDACTED]",
      sample_rate: "1",
      sampled: "true",
    });
  });
});

describe("browser tracing decision (OBS-02)", () => {
  it("leaves the sampling decision of the API's requests to the API", async () => {
    // Any defined rate, even 0, makes the browser send "not sampled" with its /api/v1 requests, and the
    // API then never traces a request made from a page (tests/unit/test_sentry_tracing.py pins the API side).
    const options = await optionsOf("./instrumentation-client");
    expect(options).not.toHaveProperty("tracesSampleRate");
    expect(options).not.toHaveProperty("tracesSampler");
    expect(options).not.toHaveProperty("tracePropagationTargets");
  });

  it("hands App Router navigations to Sentry", async () => {
    const { onRouterTransitionStart } = await import(/* @vite-ignore */ "./instrumentation-client");
    expect(onRouterTransitionStart).toBe(captureRouterTransitionStart);
  });
});
