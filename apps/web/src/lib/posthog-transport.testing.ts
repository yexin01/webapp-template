/**
 * Test support for leakage tests with the real PostHog SDK (not a test file
 * itself). Call installPostHogTransport() before posthog-js is first loaded:
 * the SDK keeps the fetch it finds when it loads.
 */
import { gunzipSync } from "node:zlib";

export type SentRequest = { url: string; body: unknown };
export type SentEvent = {
  event: string;
  properties: Record<string, unknown>;
  timestamp?: string;
  $set?: Record<string, unknown>;
  $set_once?: Record<string, unknown>;
  $unset?: string[];
};

/** Captures every request the SDK makes (fetch, sendBeacon, XHR) instead of sending it. */
export function installPostHogTransport(): SentRequest[] {
  const requests: SentRequest[] = [];
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
    _m: string,
    url: string | URL,
  ) {
    this.target = String(url);
  };
  XMLHttpRequest.prototype.send = function (this: XMLHttpRequest & { target?: string }, body?: unknown) {
    requests.push({ url: this.target ?? "", body });
  };
  return requests;
}

export async function decode(body: unknown): Promise<string> {
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

/** Drains the SDK's batch queue, then returns everything sent: the raw text and the events. */
export async function transmitted(requests: SentRequest[]): Promise<{ text: string; events: SentEvent[] }> {
  window.dispatchEvent(new Event("pagehide"));
  await new Promise((resolve) => setTimeout(resolve, 50));
  const texts = await Promise.all(requests.map(async ({ url, body }) => `${url}\n${await decode(body)}`));
  const events = texts.flatMap((text) => {
    const json = text.slice(text.indexOf("\n") + 1);
    if (!json.startsWith("{")) return [];
    const payload = JSON.parse(json) as { batch?: SentEvent[] } & Partial<SentEvent>;
    return payload.batch ?? (payload.event ? [payload as SentEvent] : []);
  });
  return { text: texts.join("\n"), events };
}

/** PostHog storage as user A's earlier, identified visit left it in this browser. */
export function storeEarlierIdentifiedUser(key: string) {
  localStorage.setItem(
    `ph_${key}_posthog`,
    JSON.stringify({ distinct_id: "internal-a", $device_id: "device-a", $user_state: "identified" }),
  );
  // A foreground tab, as in a real browser (jsdom reports "prerender", which defers the first pageview).
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
}

/** Nothing in an event ties it to the stored user A. */
export function isNotEarlierUser(event: SentEvent): boolean {
  const { distinct_id, $device_id, $anon_distinct_id } = event.properties;
  return distinct_id !== "internal-a" && $device_id !== "device-a" && $anon_distinct_id !== "internal-a";
}
