// @vitest-environment node
/**
 * OBS-08 leakage test with the real server SDK (not mocked), initialized by
 * sentry.server.config.ts with tracing on: a sampled request span carries the
 * raw request URL as Next.js records it, and every envelope the SDK would
 * send is captured at the transport and searched for the canary, headers
 * included, as are the trace headers it adds to outgoing requests.
 */
import * as Sentry from "@sentry/nextjs";

const TICKET = "CANARYTICKET42";

const sent = vi.hoisted((): unknown[] => []);
vi.mock("@sentry/nextjs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@sentry/nextjs")>();
  const transport = () => ({
    send: async (envelope: unknown) => {
      sent.push(envelope);
      return {};
    },
    flush: async () => true,
  });
  return {
    ...actual,
    // Not enumerable on the CommonJS server build's namespace, so not copied by the spread.
    flush: actual.flush,
    getClient: actual.getClient,
    captureException: actual.captureException,
    continueTrace: actual.continueTrace,
    getTraceData: actual.getTraceData,
    init: (options: Parameters<typeof actual.init>[0]) => actual.init({ ...options, transport }),
  };
});
vi.mock("./lib/env.server", () => ({
  loadServerEnv: () => ({
    appEnv: "test",
    errorReporting: { enabled: true, dsn: "https://key@o1.ingest.sentry.io/1", tracesSampleRate: 1 },
  }),
}));

/** The trace context (dynamic sampling context) in each sent envelope's headers. */
const traceHeaders = () =>
  sent.map((envelope) => (envelope as [{ trace?: { transaction?: string } }])[0].trace);

beforeAll(async () => {
  await import("./sentry.server.config");
});

beforeEach(() => {
  sent.length = 0;
});

describe("Sentry server transport (real SDK)", () => {
  it("never sends a request URL's query string or fragment in a span", async () => {
    const target = `/sign-up?__clerk_ticket=${TICKET}&__clerk_status=sign_up`;
    Sentry.startSpan(
      {
        name: `GET ${target}`,
        attributes: {
          "next.span_type": "BaseServer.handleRequest",
          "http.method": "GET",
          "http.target": target,
          "http.url": `https://app.example.com/billing/success?session_id=${TICKET}#${TICKET}`,
        },
      },
      () => undefined,
    );
    await Sentry.flush(2000);

    const text = JSON.stringify(sent);
    // Not vacuous: the span was sent, with its URLs stripped rather than dropped.
    expect(text).toContain('"http.target":{"value":"/sign-up"');
    expect(text).toContain('"http.url":{"value":"https://app.example.com/billing/success"');
    expect(text).not.toContain(TICKET);
  });

  it("never sends a root span's query string in the trace context of envelope or request headers", async () => {
    // A root span the SDK does not rename, as Next.js's "GET /_error" becomes "GET <raw URL>".
    let baggage = "";
    Sentry.startSpan({ name: `GET /billing/success?session_id=${TICKET}#${TICKET}` }, () => {
      baggage = Sentry.getTraceData().baggage ?? "";
      Sentry.captureException(new Error("Checkout confirmation failed"));
    });
    await Sentry.flush(2000);

    // Not vacuous: an error and a span were sent, each naming the root span by its path.
    expect(sent).toHaveLength(2);
    expect(traceHeaders().map((trace) => trace?.transaction)).toEqual([
      "GET /billing/success",
      "GET /billing/success",
    ]);
    expect(baggage).toContain("sentry-transaction=GET%20%2Fbilling%2Fsuccess,");
    expect(JSON.stringify(sent)).not.toContain(TICKET);
    expect(baggage).not.toContain(TICKET);
  });

  // The SDK still forwards an upstream trace context unchanged in the baggage of outgoing requests.
  it("never sends Sentry a query string from a trace context received from upstream", async () => {
    const baggage = Object.entries({
      "sentry-trace_id": "0123456789abcdef0123456789abcdef",
      "sentry-public_key": "key",
      "sentry-sampled": "true",
      "sentry-transaction": `GET /sign-up?__clerk_ticket=${TICKET}`,
      "sentry-release": `web@1 /sign-up?__clerk_ticket=${TICKET}`,
    })
      .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
      .join(",");
    Sentry.continueTrace(
      { sentryTrace: "0123456789abcdef0123456789abcdef-0123456789abcdef-1", baggage },
      () => Sentry.startSpan({ name: "GET /dashboard" }, () => undefined),
    );
    await Sentry.flush(2000);

    // Not vacuous: the span was sent under the upstream trace context, its name kept to the path.
    expect(traceHeaders()).toEqual([expect.objectContaining({ transaction: "GET /sign-up" })]);
    expect(JSON.stringify(sent)).not.toContain(TICKET);
  });
});
