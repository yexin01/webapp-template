/**
 * OBS-03 with the real PostHog SDK (transport captured, not mocked): the
 * allowlist holds for posthog-js called directly, not only through track().
 * One scenario per file: it needs a fresh SDK instance and storage.
 */
import { installPostHogTransport, transmitted, type SentEvent } from "./posthog-transport.testing";

vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));

const EMAIL = "canary.person@example.com";
// The SDK keeps the fetch it finds when it loads, so it is imported after the transport is in place.
const sent = installPostHogTransport();
const named = (events: SentEvent[], name: string) => events.filter((e) => e.event === name);
const EXPECTED = [
  "$identify",
  "$pageview",
  "billing_portal_clicked",
  "integration_test_sent",
  "paywall_viewed",
  "upgrade_clicked",
];

/** What was transmitted once the SDK has sent everything it will, however slowly a loaded machine runs it. */
async function transmittedEventually() {
  let result = await transmitted(sent);
  for (
    let attempt = 0;
    attempt < 50 && !EXPECTED.every((name) => named(result.events, name).length);
    attempt++
  ) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    result = await transmitted(sent);
  }
  return result;
}

describe("posthog-js used directly", () => {
  it("sends only allowlisted events and SDK events, with only allowlisted properties and no personal data", async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    const posthog = (await import("posthog-js")).default;
    const { identify, startAnalytics } = await import("./analytics");
    startAnalytics();
    await new Promise((resolve) => setTimeout(resolve, 50));
    // Identifies with personal data first; the facade then replaces that identity with the internal ID.
    posthog.identify("internal-c", { email: EMAIL }, { name: EMAIL });
    identify("internal-b");

    // Every other way the SDK takes personal data, except through the facade.
    posthog.capture("project_renamed", { email: EMAIL, name: "Secret plan" });
    posthog.register({ email: EMAIL });
    posthog.capture("upgrade_clicked", { plan: "pro_monthly", note: EMAIL, email: EMAIL });
    posthog.capture("paywall_viewed", { feature: { email: EMAIL } }, { $set: { email: EMAIL } });
    posthog.capture("billing_portal_clicked", { $set: { email: EMAIL }, $set_once: { email: EMAIL } });
    posthog.capture(
      "integration_test_sent",
      { source: "web" },
      { $set_once: { email: EMAIL, $initial_x: "kept" } },
    );
    posthog.capture("$set", { $set: { email: EMAIL } });
    posthog.capture("$pageview", { email: EMAIL, utm_source: EMAIL });
    posthog.setPersonProperties({ email: EMAIL }, { name: EMAIL });
    posthog.group("company", "acme", { email: EMAIL });
    posthog.alias(EMAIL);

    const { text, events } = await transmittedEventually();
    expect(text).not.toContain(EMAIL);
    expect(text).not.toContain("Secret plan");
    const names = new Set(events.map((e) => e.event));
    // The test is not vacuous: what the allowlist admits still arrives.
    expect([...names].sort()).toEqual([...EXPECTED].sort());
    // The batch is not ordered.
    expect(
      named(events, "$identify")
        .map((e) => e.properties.distinct_id)
        .sort(),
    ).toEqual(["internal-b", "internal-c"]);
    expect(named(events, "upgrade_clicked")[0]?.properties).toMatchObject({ plan: "pro_monthly" });
    expect(named(events, "upgrade_clicked")[0]?.properties).not.toHaveProperty("note");
    expect(named(events, "paywall_viewed")[0]?.properties).not.toHaveProperty("feature");
    expect(named(events, "integration_test_sent")[0]?.$set_once).toEqual({ $initial_x: "kept" });
    for (const event of events) {
      expect(event.properties.distinct_id).toBeDefined();
      expect(event.properties.token).toBe("phc_test");
    }
  });
});
