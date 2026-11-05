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

type Envelope = [{ trace?: { transaction?: string } }, Array<[{ type: string }, unknown]>];

/** What the tests below count: an error, a transaction, or a span. */
const DATA_ITEMS = new Set(["event", "transaction", "span"]);

/**
 * The envelopes that carry an error or a span. When the SDK finds a release (GITHUB_SHA on GitHub Actions,
 * VERCEL_GIT_COMMIT_SHA on Vercel, COMMIT_REF, SENTRY_RELEASE) it also sends a release-health `session` envelope
 * as soon as an error is captured, and finds none on a machine without one: a count of everything sent would
 * depend on where the test runs (it failed on the first remote run of CI, 2026-11-01). The canary search below
 * still covers every envelope, session envelopes included.
 */
const dataEnvelopes = () =>
  (sent as Envelope[]).filter(([, items]) => items.some(([header]) => DATA_ITEMS.has(header.type)));

/** The trace context (dynamic sampling context) in the headers of each envelope that carries data. */
const traceHeaders = () => dataEnvelopes().map(([headers]) => headers.trace);

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
    expect(dataEnvelopes()).toHaveLength(2);
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
