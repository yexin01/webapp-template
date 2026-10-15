/**
 * OBS-03 / OBS-08 leakage test with the real PostHog SDK (not mocked): a
 * visitor lands on a URL whose query string and fragment carry a token, from
 * a referrer that does too, and every request the SDK would send is captured
 * at the transport and searched for the canary.
 */
import { gunzipSync } from "node:zlib";

import posthog from "posthog-js";

import { identify, resetAnalytics, sendTestEvent, startAnalytics, track } from "./analytics";

const CANARY = "CANARY7f3a9";

// The SDK keeps the fetch it finds when it loads, so the transports are replaced before any import.
const sent = vi.hoisted(() => {
  const requests: { url: string; body: unknown }[] = [];
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    requests.push({ url: String(url), body: init?.body });
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  Object.defineProperty(navigator, "sendBeacon", {
    configurable: true,
    value: (url: string, body: unknown) => {
      requests.push({ url, body });
      return true;
    },
  });
  XMLHttpRequest.prototype.open = function (
    this: XMLHttpRequest & { target?: string },
    _method: string,
    url: string | URL,
  ) {
    this.target = String(url);
  };
  XMLHttpRequest.prototype.send = function (this: XMLHttpRequest & { target?: string }, body?: unknown) {
    requests.push({ url: this.target ?? "", body });
  };
  return requests;
});

vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));

async function decode(body: unknown): Promise<string> {
  if (body === undefined || body === null) return "";
  if (typeof body === "string") return body;
  if (body instanceof Blob) return decode(new Uint8Array(await body.arrayBuffer()));
  if (body instanceof ArrayBuffer) return decode(new Uint8Array(body));
  if (body instanceof Uint8Array) {
    const bytes = Buffer.from(body);
    return bytes[0] === 0x1f && bytes[1] === 0x8b ? gunzipSync(bytes).toString() : bytes.toString();
  }
  return String(body);
}

type SentEvent = { event: string; properties: Record<string, unknown>; $set_once?: Record<string, unknown> };

/** Everything sent so far, decoded: request URLs and bodies, and the events in them. */
async function transmitted() {
  window.dispatchEvent(new Event("pagehide")); // drains the batch queue through sendBeacon
  await new Promise((resolve) => setTimeout(resolve, 50));
  const texts = await Promise.all(sent.map(async ({ url, body }) => `${url}\n${await decode(body)}`));
  const events = texts.flatMap((text) => {
    const json = text.slice(text.indexOf("\n") + 1);
    if (!json.startsWith("{")) return [];
    const payload = JSON.parse(json) as { batch?: SentEvent[] } & Partial<SentEvent>;
    return payload.batch ?? (payload.event ? [payload as SentEvent] : []);
  });
  return { text: texts.join("\n"), events };
}

beforeAll(() => {
  window.history.replaceState(
    {},
    "",
    `/sign-up?__clerk_ticket=${CANARY}&redirect_url=%2Fdashboard#${CANARY}`,
  );
  Object.defineProperty(document, "referrer", {
    configurable: true,
    value: `https://mail.example.com/inbox?token=${CANARY}#${CANARY}`,
  });
  startAnalytics();
});

beforeEach(() => {
  sent.length = 0;
});

describe("PostHog transport (real SDK)", () => {
  it("never sends a URL's query string or fragment", async () => {
    identify("internal-user-1");
    track("upgrade_clicked", { plan: "pro_monthly" });
    expect(sendTestEvent()).toBe("sent");
    // A client-side navigation to a URL with its own token.
    window.history.pushState({}, "", `/billing/success?session_id=${CANARY}#${CANARY}`);
    await new Promise((resolve) => setTimeout(resolve, 20));

    const { text, events } = await transmitted();
    // The test is not vacuous: the SDK sent these events, with their URL properties scrubbed rather than dropped.
    const names = events.map((e) => e.event);
    expect(names).toEqual(
      expect.arrayContaining(["$identify", "upgrade_clicked", "integration_test_sent", "$pageview"]),
    );
    const identifyEvent = events.find((e) => e.event === "$identify");
    expect(identifyEvent?.$set_once?.$initial_current_url).toBe("http://localhost:3000/sign-up");
    expect(identifyEvent?.properties.$session_entry_url).toBe("http://localhost:3000/sign-up");
    expect(identifyEvent?.properties.$session_entry_referrer).toBe("https://mail.example.com/inbox");
    expect(text).not.toContain(CANARY);
    expect(sent.some(({ url }) => url.includes("/flags"))).toBe(false);
  });

  it("starts a new person and device when the identity is reset or replaced", async () => {
    identify("internal-user-1");
    const device = posthog.get_property("$device_id") as string;
    expect(posthog._isIdentified()).toBe(true);

    // Signed out: nothing ties later anonymous events to user 1.
    resetAnalytics({ onlyIfIdentified: true });
    expect(posthog._isIdentified()).toBe(false);
    expect(posthog.get_distinct_id()).not.toBe("internal-user-1");
    expect(posthog.get_property("$device_id")).not.toBe(device);

    // Another person is identified without being merged into user 1.
    identify("internal-user-1");
    identify("internal-user-2");
    const { events } = await transmitted();
    const second = events.find(
      (e) => e.event === "$identify" && e.properties.distinct_id === "internal-user-2",
    );
    expect(second?.properties.$anon_distinct_id).toBeDefined();
    expect(second?.properties.$anon_distinct_id).not.toBe("internal-user-1");
  });
});
