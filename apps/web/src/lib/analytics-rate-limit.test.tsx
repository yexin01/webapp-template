/**
 * OBS-07 with the real PostHog SDK (transport captured, not mocked): a loop that tracks an allowlisted event
 * is cut off at the client-side limit, so it cannot flood PostHog. One scenario per file: it needs a fresh SDK
 * instance and storage.
 */
import { installPostHogTransport, transmitted } from "./posthog-transport.testing";

vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));

// The SDK keeps the fetch it finds when it loads, so it is imported after the transport is in place.
const sent = installPostHogTransport();

/** analytics.ts: an average of 10 events a second, and a burst of 100. */
const EVENTS_PER_SECOND = 10;
const BURST = 100;

describe("the client-side event limit (OBS-07)", () => {
  it("lets a burst through and drops the rest of a runaway loop, without reporting the drop", async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    const { startAnalytics, track } = await import("./analytics");
    startAnalytics();
    await new Promise((resolve) => setTimeout(resolve, 50));

    // The SDK says so on the console for every call it drops.
    const dropped = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const began = Date.now();
    for (let i = 0; i < 3 * BURST; i++) track("paywall_viewed", { feature: "pro_insight" });
    // The bucket refills while the loop runs: a loaded machine is allowed what the elapsed time earns.
    const refilled = Math.ceil(((Date.now() - began) / 1000) * EVENTS_PER_SECOND);

    const { events } = await transmitted(sent);
    const tracked = events.filter((event) => event.event === "paywall_viewed").length;
    // Not vacuous: the burst arrives, and the SDK itself dropped the rest.
    expect(tracked).toBeGreaterThan(BURST / 2);
    expect(dropped.mock.calls.flat().join(" ")).toContain("ignored due to client rate limiting");
    dropped.mockRestore();
    expect(tracked, "events beyond the limit were sent").toBeLessThanOrEqual(BURST + refilled);
    // The SDK reports a drained bucket with an event of its own; it is not on the allowlist, so it is dropped too.
    expect(events.map((event) => event.event)).not.toContain("$$client_ingestion_warning");
  });
});
