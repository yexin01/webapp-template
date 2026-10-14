import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";

import { ApiError } from "@/lib/api/errors";

import { CheckoutConfirmation } from "./checkout-confirmation";

const api = vi.hoisted(() => ({ billingStatus: vi.fn() }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));

const free = { plan: "free", state: "processing" };

beforeEach(() => {
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
});
