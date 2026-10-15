import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

import { BillingPanel } from "./billing-panel";

const api = vi.hoisted(() => ({ billingStatus: vi.fn(), startCheckout: vi.fn(), openPortal: vi.fn() }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("@/lib/analytics", () => ({ track: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/billing",
  useSearchParams: () => new URLSearchParams(),
}));

const free: Schemas["BillingStatus"] = {
  plan: "free",
  state: "none",
  plan_key: null,
  current_period_end: null,
  cancels_at: null,
  available_plans: ["pro_monthly"],
};
const pro: Schemas["BillingStatus"] = {
  plan: "pro",
  state: "active",
  plan_key: "pro_monthly",
  current_period_end: "2026-11-15T00:00:00Z",
  cancels_at: null,
  available_plans: ["pro_monthly"],
};

function pageShow(persisted: boolean) {
  const event = new Event("pageshow") as PageTransitionEvent;
  Object.defineProperty(event, "persisted", { value: persisted });
  act(() => {
    window.dispatchEvent(event);
  });
}

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_1";
  api.billingStatus.mockReset();
});

describe("billing panel", () => {
  it("requests the billing status once on a fresh page load", async () => {
    api.billingStatus.mockResolvedValue(free);
    auth.isLoaded = false;
    auth.userId = undefined;
    const { rerender } = render(<BillingPanel />);
    auth.isLoaded = true;
    auth.userId = "user_1";
    rerender(<BillingPanel />);
    expect(await screen.findByText(/You are on the Free plan/)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.billingStatus).toHaveBeenCalledOnce();
  });

  it("re-enables checkout when the page is restored from the back/forward cache", async () => {
    api.billingStatus.mockResolvedValue(free);
    api.startCheckout.mockResolvedValue({ url: "https://checkout.stripe.com/c/pay/cs_test_1" });
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      assign,
      origin: window.location.origin,
    });

    render(<BillingPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Pro — monthly" }));
    expect(assign).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_test_1");
    // The browser leaves for Stripe with the button still marked as redirecting.
    expect(screen.getByRole("button", { name: "Redirecting…" })).toBeDisabled();

    pageShow(false);
    expect(screen.getByRole("button", { name: "Redirecting…" })).toBeDisabled();
    // The visitor presses Back and the page comes back from the cache, with the status fetched again.
    pageShow(true);
    expect(await screen.findByRole("button", { name: "Pro — monthly" })).toBeEnabled();
    expect(api.billingStatus).toHaveBeenCalledTimes(2);
  });

  it("shows the current status, not the one from before checkout, when restored from the back/forward cache", async () => {
    api.billingStatus.mockResolvedValueOnce(free).mockResolvedValue(pro);
    api.startCheckout.mockResolvedValue({ url: "https://checkout.stripe.com/c/pay/cs_test_1" });
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      assign: vi.fn(),
      origin: window.location.origin,
    });
    render(<BillingPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Pro — monthly" }));

    pageShow(false);
    expect(api.billingStatus).toHaveBeenCalledOnce();
    // The visitor paid on Stripe, then pressed Back instead of following the return link.
    pageShow(true);
    expect(screen.queryByRole("button", { name: /Pro — monthly|Redirecting/ })).toBeNull();
    expect(await screen.findByText(/Your Pro plan renews on/)).toBeInTheDocument();
    expect(screen.queryByText(/You are on the Free plan/)).toBeNull();
    expect(api.billingStatus).toHaveBeenCalledTimes(2);
  });

  it("shows the current plan when checkout is refused because Pro is already active (BILL-07)", async () => {
    api.billingStatus.mockResolvedValueOnce(free).mockResolvedValue(pro);
    api.startCheckout.mockRejectedValue(
      new ApiError(409, "CONFLICT", "You already have Pro. Manage your plan from the billing page.", null),
    );
    render(<BillingPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Pro — monthly" }));

    // The stale Free view is replaced by the server's current state, with plan management.
    expect(await screen.findByRole("button", { name: "Manage billing" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Pro — monthly" })).toBeNull();
    expect(api.billingStatus).toHaveBeenCalledTimes(2);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("You already have Pro. Manage your plan from the billing page.");
    expect(alert).not.toHaveTextContent("Check your input");
  });
});
