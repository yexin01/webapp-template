import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/lib/api/client";

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
    // The visitor presses Back and the page comes back from the cache.
    pageShow(true);
    expect(screen.getByRole("button", { name: "Pro — monthly" })).toBeEnabled();
  });
});
