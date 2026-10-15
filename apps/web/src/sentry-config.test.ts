/**
 * OBS-02 / OBS-08: the browser and server Sentry configurations scrub what
 * they send. Synthetic canaries go through each SDK's beforeSend and
 * beforeBreadcrumb hooks exactly as configured, and must not come out.
 */
import type { Breadcrumb, ErrorEvent } from "@sentry/nextjs";

const init = vi.hoisted(() => vi.fn());
vi.mock("@sentry/nextjs", () => ({ init }));
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
};

async function optionsOf(module: string): Promise<Options> {
  init.mockClear();
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

  it("scrubs the event message, extra data, exception, request, user, and breadcrumbs", async () => {
    const { beforeSend } = await optionsOf(module);
    const scrubbed = beforeSend(event(), {});
    expectClean(scrubbed);
    expect(scrubbed?.request?.url).toBe("https://app.example.com/billing/success");
    expect(scrubbed?.breadcrumbs).toHaveLength(3);
    expect(scrubbed?.message).toBe("Checkout failed for [REDACTED]");
  });
});
