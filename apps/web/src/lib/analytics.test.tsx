import { render, waitFor } from "@testing-library/react";

import { AnalyticsProvider } from "./analytics-provider";

const posthog = vi.hoisted(() => ({ init: vi.fn(), identify: vi.fn(), reset: vi.fn(), capture: vi.fn() }));
const auth = vi.hoisted(() => ({ userId: null as string | null }));
const api = vi.hoisted(() => ({ me: vi.fn() }));

vi.mock("posthog-js", () => ({ default: posthog }));
vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
// A new client object on every render, as when Clerk hands out a new getToken.
vi.mock("@/lib/api/use-api", () => ({ useApi: () => ({ me: api.me }) }));

const INTERNAL_IDS: Record<string, string> = { user_a: "internal-a", user_b: "internal-b" };

beforeEach(() => {
  auth.userId = null;
  api.me.mockReset().mockImplementation(async () => ({ user_id: INTERNAL_IDS[auth.userId ?? ""] }));
  posthog.identify.mockClear();
  posthog.reset.mockClear();
});

describe("analytics", () => {
  it("pins remotely controllable capture off (OBS-03)", () => {
    render(<AnalyticsProvider />);
    expect(posthog.init).toHaveBeenCalledOnce();
    const [key, options] = posthog.init.mock.calls[0] ?? [];
    expect(key).toBe("phc_test");
    expect(options).toMatchObject({
      api_host: "https://eu.i.posthog.com",
      autocapture: false,
      rageclick: false,
      capture_exceptions: false,
      capture_heatmaps: false,
      capture_dead_clicks: false,
      capture_performance: false,
      logs: { captureConsoleLogs: false },
      disable_session_recording: true,
      disable_surveys: true,
    });
  });

  it("identifies by internal ID and resets when the signed-in user changes", async () => {
    auth.userId = "user_a";
    const { rerender } = render(<AnalyticsProvider />);
    await waitFor(() => expect(posthog.identify).toHaveBeenCalledWith("internal-a"));
    expect(posthog.reset).not.toHaveBeenCalled();

    // Another account in the same browser (Clerk multi-session switch).
    auth.userId = "user_b";
    rerender(<AnalyticsProvider />);
    await waitFor(() => expect(posthog.identify).toHaveBeenLastCalledWith("internal-b"));
    expect(posthog.reset).toHaveBeenCalledOnce();
    expect(posthog.reset.mock.invocationCallOrder[0]).toBeLessThan(
      posthog.identify.mock.invocationCallOrder[1] ?? 0,
    );

    auth.userId = null;
    rerender(<AnalyticsProvider />);
    expect(posthog.reset).toHaveBeenCalledTimes(2);
    expect(posthog.identify).toHaveBeenCalledTimes(2);
  });

  it("does not re-identify the same user when the API client changes", async () => {
    auth.userId = "user_a";
    const { rerender } = render(<AnalyticsProvider />);
    await waitFor(() => expect(posthog.identify).toHaveBeenCalledOnce());
    rerender(<AnalyticsProvider />);
    expect(api.me).toHaveBeenCalledOnce();
    expect(posthog.reset).not.toHaveBeenCalled();
  });
});
