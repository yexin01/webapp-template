/**
 * OBS-03 with the real PostHog SDK (transport captured, not mocked): events
 * tracked before the signed-in user's identity settles are kept and sent as
 * that user. The SDK instance outlives vi.resetModules() (node_modules are not
 * reloaded), so each test starts it from a reset identity.
 */
import { act, render } from "@testing-library/react";

import { installPostHogTransport, transmitted, type SentEvent } from "./posthog-transport.testing";

const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_b" as string | null }));
/** Settles the lookup of the internal user ID (/me) when the test says so. */
const lookup = vi.hoisted(() => ({
  resolve: undefined as unknown as (userId: string) => void,
  pending: Promise.resolve("never"),
}));
vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
vi.mock("@/lib/api/use-api", () => ({
  useApi: () => ({ me: async () => ({ user_id: await lookup.pending }) }),
}));

const sent = installPostHogTransport();

type Facade = typeof import("./analytics");
type Provider = typeof import("./analytics-provider");

/** A fresh SDK instance and facade, with /me unresolved until `lookup.resolve` is called. */
async function load(): Promise<{ facade: Facade; provider: Provider }> {
  vi.resetModules();
  localStorage.clear();
  sessionStorage.clear();
  const posthog = (await import("posthog-js")).default;
  if (posthog.__loaded) posthog.reset(true);
  sent.length = 0;
  // A foreground tab, as in a real browser (jsdom reports "prerender", which defers the first pageview).
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  lookup.pending = new Promise<string>((resolve) => (lookup.resolve = resolve));
  return { facade: await import("./analytics"), provider: await import("./analytics-provider") };
}

const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 30)));
const named = (events: SentEvent[], name: string) => events.filter((e) => e.event === name);

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_b";
});

describe("events tracked before the user's identity settles", () => {
  it("are sent once it has, as that user and with the time they happened", async () => {
    const { facade, provider } = await load();
    const view = render(<provider.AnalyticsProvider />);
    const before = Date.now();
    facade.track("paywall_viewed", { feature: "pro_insight" });
    await settle();
    expect(sent).toEqual([]);

    lookup.resolve("internal-b");
    await settle();
    const { events } = await transmitted(sent);
    const [paywall, ...others] = named(events, "paywall_viewed");
    expect(others).toEqual([]);
    expect(paywall?.properties).toMatchObject({ feature: "pro_insight", distinct_id: "internal-b" });
    // The SDK stamps the capture with `timestamp`, not the moment the buffer was flushed.
    expect(new Date(paywall?.timestamp ?? "").getTime()).toBeLessThan(Date.now() - 20);
    expect(new Date(paywall?.timestamp ?? "").getTime()).toBeGreaterThanOrEqual(before - 1);
    expect(named(events, "$identify")).toHaveLength(1);
    view.unmount();
  });

  it("are sent for a signed-out visitor once Clerk has loaded", async () => {
    const { facade, provider } = await load();
    auth.isLoaded = false;
    auth.userId = null;
    const view = render(<provider.AnalyticsProvider />);
    facade.track("upgrade_clicked", { plan: "pro_monthly" });
    await settle();
    expect(sent).toEqual([]);

    auth.isLoaded = true;
    view.rerender(<provider.AnalyticsProvider />);
    await settle();
    expect(named((await transmitted(sent)).events, "upgrade_clicked")).toHaveLength(1);
  });

  it("are sent on the anonymous identity when the lookup of the internal ID fails", async () => {
    const { facade, provider } = await load();
    lookup.pending = Promise.reject(new Error("unavailable"));
    lookup.pending.catch(() => undefined);
    render(<provider.AnalyticsProvider />);
    facade.track("billing_portal_clicked");
    await settle();

    const { events } = await transmitted(sent);
    const [portal] = named(events, "billing_portal_clicked");
    expect(portal).toBeDefined();
    expect(named(events, "$identify")).toEqual([]);
  });

  it("are not sent as the next user when the signed-in user changes before identity settles", async () => {
    const { facade, provider } = await load();
    auth.userId = "user_a";
    const view = render(<provider.AnalyticsProvider />);
    facade.track("upgrade_clicked", { plan: "tracked_as_a" });
    auth.userId = "user_b";
    view.rerender(<provider.AnalyticsProvider />);
    facade.track("upgrade_clicked", { plan: "tracked_as_b" });
    lookup.resolve("internal-b");
    await settle();

    const { events } = await transmitted(sent);
    expect(named(events, "upgrade_clicked").map((e) => e.properties.plan)).toEqual(["tracked_as_b"]);
    view.unmount();
  });

  it("keep at most 20, then drop the newest", async () => {
    const { facade, provider } = await load();
    const view = render(<provider.AnalyticsProvider />);
    for (let i = 1; i <= 21; i++) facade.track("upgrade_clicked", { plan: `plan_${i}` });
    lookup.resolve("internal-b");
    await settle();

    const plans = named((await transmitted(sent)).events, "upgrade_clicked").map((e) => e.properties.plan);
    expect(plans).toHaveLength(20);
    expect(plans).toContain("plan_20");
    expect(plans).not.toContain("plan_21");
    view.unmount();
  });

  it("can be discarded, and are not kept while analytics is off", async () => {
    const { facade, provider } = await load();
    const view = render(<provider.AnalyticsProvider />);
    facade.track("upgrade_clicked", { plan: "declined" });
    facade.discardPendingEvents();
    lookup.resolve("internal-b");
    await settle();
    expect(named((await transmitted(sent)).events, "upgrade_clicked")).toEqual([]);
    view.unmount();
  });
});
