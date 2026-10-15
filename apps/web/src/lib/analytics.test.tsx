import { render, waitFor } from "@testing-library/react";

import { AnalyticsProvider } from "./analytics-provider";

const posthog = vi.hoisted(() => ({
  init: vi.fn(),
  identify: vi.fn(),
  reset: vi.fn(),
  capture: vi.fn(),
  _isIdentified: vi.fn(() => false),
  get_distinct_id: vi.fn(() => "anonymous-id"),
}));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: null as string | null }));
const api = vi.hoisted(() => ({ me: vi.fn() }));
const analytics = vi.hoisted(() => ({ enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" }));

vi.mock("posthog-js", () => ({ default: posthog }));
vi.mock("./env", () => ({ publicEnv: { analytics } }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
// A new client object on every render, as when Clerk hands out a new getToken.
vi.mock("@/lib/api/use-api", () => ({ useApi: () => ({ me: api.me }) }));

const INTERNAL_IDS: Record<string, string> = { user_a: "internal-a", user_b: "internal-b" };

beforeEach(() => {
  analytics.enabled = true;
  auth.isLoaded = true;
  auth.userId = null;
  api.me.mockReset().mockImplementation(async () => ({ user_id: INTERNAL_IDS[auth.userId ?? ""] }));
  posthog.identify.mockClear();
  posthog.reset.mockClear();
  posthog._isIdentified.mockReset().mockReturnValue(false);
  posthog.get_distinct_id.mockReset().mockReturnValue("anonymous-id");
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
    expect(options.before_send).toBeTypeOf("function");
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
    // A new device ID too, so the next person is not linked to the previous one.
    expect(posthog.reset).toHaveBeenCalledWith(true);
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

  it("drops an identity kept from an earlier visit when the page loads signed out", () => {
    // PostHog restored user A's identity from storage; nobody is signed in now.
    posthog._isIdentified.mockReturnValue(true);
    posthog.get_distinct_id.mockReturnValue("internal-a");
    render(<AnalyticsProvider />);
    expect(posthog.reset).toHaveBeenCalledOnce();
    expect(posthog.reset).toHaveBeenCalledWith(true);
    expect(api.me).not.toHaveBeenCalled();
  });

  it("keeps an anonymous visitor's identity and waits for Clerk to load", () => {
    render(<AnalyticsProvider />);
    expect(posthog.reset).not.toHaveBeenCalled();

    posthog._isIdentified.mockReturnValue(true);
    auth.isLoaded = false;
    render(<AnalyticsProvider />);
    expect(posthog.reset).not.toHaveBeenCalled();
    expect(api.me).not.toHaveBeenCalled();
  });

  it("clears another person's stored identity before identifying the signed-in user", async () => {
    posthog._isIdentified.mockReturnValue(true);
    posthog.get_distinct_id.mockReturnValue("internal-a");
    auth.userId = "user_b";
    render(<AnalyticsProvider />);
    await waitFor(() => expect(posthog.identify).toHaveBeenCalledWith("internal-b"));
    expect(posthog.reset).toHaveBeenCalledOnce();
    expect(posthog.reset.mock.invocationCallOrder[0]).toBeLessThan(
      posthog.identify.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it("keeps a returning user's own identity", async () => {
    posthog._isIdentified.mockReturnValue(true);
    posthog.get_distinct_id.mockReturnValue("internal-a");
    auth.userId = "user_a";
    render(<AnalyticsProvider />);
    await waitFor(() => expect(posthog.identify).toHaveBeenCalledWith("internal-a"));
    expect(posthog.reset).not.toHaveBeenCalled();
  });

  it("does not look up the user when analytics is disabled", () => {
    analytics.enabled = false;
    auth.userId = "user_a";
    render(<AnalyticsProvider />);
    expect(api.me).not.toHaveBeenCalled();
  });
});
