import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";

import { ApiError } from "@/lib/api/errors";

import { CheckoutConfirmation } from "./checkout-confirmation";

const api = vi.hoisted(() => ({ billingStatus: vi.fn(), syncBilling: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
// One client per signed-in user, and a new one once the user arrives, as useApi does.
const clients = vi.hoisted(() => new Map<string, object>());
vi.mock("@/lib/api/use-api", () => ({
  useApi: () => {
    const user = auth.isLoaded ? (auth.userId ?? "signed-out") : "pending";
    if (!clients.has(user)) clients.set(user, {});
    return Object.assign(clients.get(user) ?? {}, api);
  },
}));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));

const free = { plan: "free", state: "processing" };

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_1";
  vi.useFakeTimers();
  api.billingStatus.mockReset();
  api.syncBilling.mockReset();
  api.syncBilling.mockResolvedValue(free);
});
afterEach(() => vi.useRealTimers());

/** Lets pending promises settle, then advances the clock. */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("checkout confirmation", () => {
  it("polls until the server reports Pro", async () => {
    api.billingStatus.mockResolvedValueOnce(free).mockResolvedValueOnce({ plan: "pro", state: "active" });
    render(<CheckoutConfirmation />);
    await advance(1500);
    expect(screen.getByText(/You are now on Pro/)).toBeInTheDocument();
    expect(api.billingStatus).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["the server is not Pro yet", () => api.billingStatus.mockResolvedValue(free)],
    [
      "requests fail",
      () => api.billingStatus.mockRejectedValue(new ApiError(503, "DEPENDENCY_UNAVAILABLE", "Down.", null)),
    ],
  ])("stops polling after unmount when %s", async (_label, arrange) => {
    arrange();
    const { unmount } = render(<CheckoutConfirmation />);
    await advance(1500);
    const calls = api.billingStatus.mock.calls.length;
    unmount();
    await advance(60_000);
    expect(api.billingStatus).toHaveBeenCalledTimes(calls);
  });

  it("starts one polling chain once Clerk has loaded", async () => {
    api.billingStatus.mockResolvedValue(free);
    auth.isLoaded = false;
    auth.userId = undefined;
    const { rerender } = render(<CheckoutConfirmation />);
    await advance(0);
    expect(api.billingStatus).not.toHaveBeenCalled();
    auth.isLoaded = true;
    auth.userId = "user_1";
    rerender(<CheckoutConfirmation />);
    await advance(0);
    expect(api.billingStatus).toHaveBeenCalledOnce();
    await advance(1500 * 3);
    expect(api.billingStatus).toHaveBeenCalledTimes(4);
  });

  it("runs a single polling chain under StrictMode", async () => {
    api.billingStatus.mockRejectedValue(new ApiError(0, "NETWORK_ERROR", "Offline.", null));
    render(
      <StrictMode>
        <CheckoutConfirmation />
      </StrictMode>,
    );
    await advance(0);
    const afterMount = api.billingStatus.mock.calls.length;
    await advance(1500 * 3);
    // One request per interval from here on, not one per mounted effect.
    expect(api.billingStatus.mock.calls.length - afterMount).toBe(3);
  });

  describe("asking the server to re-read Stripe", () => {
    it("does so once, after the short wait, and confirms on what the server read", async () => {
      api.billingStatus.mockResolvedValue(free);
      api.syncBilling.mockResolvedValue({ plan: "pro", state: "active" });
      render(<CheckoutConfirmation />);
      await advance(2_900);
      expect(api.syncBilling).not.toHaveBeenCalled();
      expect(screen.queryByText(/You are now on Pro/)).toBeNull();
      await advance(100);
      expect(api.syncBilling).toHaveBeenCalledOnce();
      expect(screen.getByText(/You are now on Pro/)).toBeInTheDocument();
      // Confirmed: the polling stops with it.
      const polls = api.billingStatus.mock.calls.length;
      await advance(30_000);
      expect(api.billingStatus).toHaveBeenCalledTimes(polls);
      expect(api.syncBilling).toHaveBeenCalledOnce();
    });

    it("is not needed when the webhook already confirmed", async () => {
      api.billingStatus.mockResolvedValue({ plan: "pro", state: "active" });
      render(<CheckoutConfirmation />);
      await advance(10_000);
      expect(screen.getByText(/You are now on Pro/)).toBeInTheDocument();
      expect(api.syncBilling).not.toHaveBeenCalled();
    });

    it.each([
      ["rate-limited", new ApiError(429, "RATE_LIMITED", "Slow down.", null)],
      ["busy", new ApiError(503, "DEPENDENCY_UNAVAILABLE", "Busy.", null)],
      ["failing", new Error("network")],
    ])("falls through to the polling when the read is %s", async (_label, failure) => {
      api.billingStatus
        .mockResolvedValueOnce(free)
        .mockResolvedValueOnce(free)
        .mockResolvedValueOnce(free)
        .mockResolvedValue({ plan: "pro", state: "active" });
      api.syncBilling.mockRejectedValue(failure);
      render(<CheckoutConfirmation />);
      await advance(3_000);
      expect(api.syncBilling).toHaveBeenCalledOnce();
      expect(screen.queryByText(/You are now on Pro/)).toBeNull();
      await advance(1_500);
      expect(screen.getByText(/You are now on Pro/)).toBeInTheDocument();
      expect(api.syncBilling).toHaveBeenCalledOnce();
    });

    it("keeps waiting when the server read finds no subscription yet", async () => {
      api.billingStatus.mockResolvedValue(free);
      render(<CheckoutConfirmation />);
      await advance(5_000);
      expect(api.syncBilling).toHaveBeenCalledOnce();
      expect(screen.getByText(/Waiting for payment confirmation/)).toBeInTheDocument();
    });

    it("never fires after unmount, and once under StrictMode", async () => {
      api.billingStatus.mockResolvedValue(free);
      const { unmount } = render(<CheckoutConfirmation />);
      await advance(2_000);
      unmount();
      await advance(10_000);
      expect(api.syncBilling).not.toHaveBeenCalled();

      render(
        <StrictMode>
          <CheckoutConfirmation />
        </StrictMode>,
      );
      await advance(5_000);
      expect(api.syncBilling).toHaveBeenCalledOnce();
    });

    it("is not sent for a user who is not the one now signed in", async () => {
      api.billingStatus.mockResolvedValue(free);
      auth.userId = "user_a";
      const { rerender } = render(<CheckoutConfirmation />);
      await advance(2_000);
      auth.userId = "user_b";
      rerender(<CheckoutConfirmation />);
      await advance(1_500);
      // User A's chain was stopped before its read; user B's own chain asks at its own 3 s.
      expect(api.syncBilling).not.toHaveBeenCalled();
      await advance(2_000);
      expect(api.syncBilling).toHaveBeenCalledOnce();
    });
  });

  it("gives up with a reassuring message after the last attempt", async () => {
    api.billingStatus.mockResolvedValue(free);
    render(<CheckoutConfirmation />);
    await advance(1500 * 25);
    expect(screen.getByText(/do not need to pay again/)).toBeInTheDocument();
    expect(api.billingStatus).toHaveBeenCalledTimes(20);
  });

  it("keeps to a 30-second budget when each request takes long", async () => {
    // Every answer takes 10 s; counting attempts would wait about 20 x 11.5 s.
    api.billingStatus.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(free), 10_000)),
    );
    render(<CheckoutConfirmation />);
    await advance(29_000);
    expect(screen.queryByText(/do not need to pay again/)).toBeNull();
    await advance(1_000);
    expect(screen.getByText(/do not need to pay again/)).toBeInTheDocument();
    const calls = api.billingStatus.mock.calls.length;
    await advance(60_000);
    expect(api.billingStatus).toHaveBeenCalledTimes(calls);
  });

  it("shows nothing of the previous user's confirmation after a Clerk account switch", async () => {
    // User A's payment is confirmed; user B is on the Free plan.
    api.billingStatus.mockImplementation(async () =>
      auth.userId === "user_a" ? { plan: "pro", state: "active" } : free,
    );
    auth.userId = "user_a";
    const { rerender } = render(<CheckoutConfirmation />);
    await advance(0);
    expect(screen.getByText(/You are now on Pro/)).toBeInTheDocument();

    // Another account in the same browser; nothing remounts the page.
    auth.userId = "user_b";
    rerender(<CheckoutConfirmation />);
    expect(screen.queryByText(/You are now on Pro/)).toBeNull();
    expect(screen.getByText(/Waiting for payment confirmation/)).toBeInTheDocument();
    await advance(1500 * 2);
    expect(screen.queryByText(/You are now on Pro/)).toBeNull();
  });

  it("gives up on time, with a reload, when Clerk never loads", async () => {
    // clerk-js was blocked or its Frontend API is down: Clerk never reports loaded.
    auth.isLoaded = false;
    auth.userId = undefined;
    render(<CheckoutConfirmation />);
    await advance(29_000);
    expect(screen.getByText(/Waiting for payment confirmation/)).toBeInTheDocument();
    await advance(1_000);
    expect(screen.getByText(/do not need to pay again/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(api.billingStatus).not.toHaveBeenCalled();
  });

  it("measures its budget from page load when Clerk loads late", async () => {
    api.billingStatus.mockResolvedValue(free);
    auth.isLoaded = false;
    auth.userId = undefined;
    const { rerender } = render(<CheckoutConfirmation />);
    await advance(20_000);
    auth.isLoaded = true;
    auth.userId = "user_1";
    rerender(<CheckoutConfirmation />);
    await advance(10_000);
    expect(screen.getByText(/do not need to pay again/)).toBeInTheDocument();
    expect(api.billingStatus).toHaveBeenCalled();
  });

  it("gives up on time while a request hangs, and still confirms a late answer", async () => {
    let answer: (value: unknown) => void = () => undefined;
    api.billingStatus.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    render(<CheckoutConfirmation />);
    await advance(30_000);
    expect(screen.getByText(/do not need to pay again/)).toBeInTheDocument();
    expect(api.billingStatus).toHaveBeenCalledOnce();
    await act(async () => answer({ plan: "pro", state: "active" }));
    expect(screen.getByText(/You are now on Pro/)).toBeInTheDocument();
  });
});
