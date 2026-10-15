import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";

import { ApiError } from "@/lib/api/errors";

import { CheckoutConfirmation } from "./checkout-confirmation";

const api = vi.hoisted(() => ({ billingStatus: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
// A new client once the user arrives, as useApi does when Clerk finishes loading.
const clients = vi.hoisted(() => ({ pending: {}, loaded: {} }));
vi.mock("@/lib/api/use-api", () => ({
  useApi: () => Object.assign(auth.isLoaded ? clients.loaded : clients.pending, api),
}));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));

const free = { plan: "free", state: "processing" };

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_1";
  vi.useFakeTimers();
  api.billingStatus.mockReset();
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
