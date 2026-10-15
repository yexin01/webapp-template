/**
 * X2 / OBS-03 with the real PostHog SDK (not mocked): this browser still holds
 * user A's identified PostHog identity from an earlier visit. Nothing may be
 * sent as A, including the SDK's automatic first pageview, while clerk-js is
 * loading or after it loads (signed-in). One scenario per file: each needs a
 * fresh SDK instance and storage.
 */
import { act, render } from "@testing-library/react";

import {
  installPostHogTransport,
  isNotEarlierUser,
  storeEarlierIdentifiedUser,
  transmitted,
} from "./posthog-transport.testing";

const auth = vi.hoisted(() => ({ isLoaded: false, userId: undefined as string | null | undefined }));
vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
vi.mock("@/lib/api/use-api", () => ({
  useApi: () => ({ me: async () => ({ user_id: auth.userId === "user_b" ? "internal-b" : "unknown" }) }),
}));

const sent = installPostHogTransport();
storeEarlierIdentifiedUser("phc_test");

it("sends a newly signed-in user's landing pageview as that user, never as the previous one", async () => {
  const { AnalyticsProvider } = await import("./analytics-provider");
  const view = render(<AnalyticsProvider />);
  // clerk-js takes a while to load; nothing may be captured meanwhile.
  await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
  auth.isLoaded = true;
  auth.userId = "user_b";
  view.rerender(<AnalyticsProvider />);
  await act(() => new Promise((resolve) => setTimeout(resolve, 50)));

  const { events } = await transmitted(sent);
  expect(events.find((e) => e.event === "$pageview")?.properties.distinct_id).toBe("internal-b");
  expect(events.filter((e) => !isNotEarlierUser(e))).toEqual([]);
});
