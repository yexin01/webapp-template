import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

import { BillingPanel } from "./billing-panel";

const api = vi.hoisted(() => ({
  billingStatus: vi.fn(),
  startCheckout: vi.fn(),
  openPortal: vi.fn(),
  syncBilling: vi.fn(),
}));
const search = vi.hoisted(() => ({ query: "" }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("@/lib/analytics", () => ({ track: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth, useClerk: () => ({ status: "ready" }) }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/billing",
  useSearchParams: () => new URLSearchParams(search.query),
}));

const free: Schemas["BillingStatus"] = {
  plan: "free",
  state: "none",
  plan_key: null,
  current_period_end: null,
  cancels_at: null,
  available_plans: ["pro_monthly"],
  checkout_blocked: null,
};
const pro: Schemas["BillingStatus"] = {
  plan: "pro",
  state: "active",
  plan_key: "pro_monthly",
  current_period_end: "2026-11-15T00:00:00Z",
  cancels_at: null,
  available_plans: ["pro_monthly"],
  checkout_blocked: "already_pro",
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
  api.startCheckout.mockReset();
  api.syncBilling.mockReset();
  api.syncBilling.mockResolvedValue(pro);
  search.query = "";
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

  it("re-reads Stripe, once, when the visitor comes back from the Customer Portal", async () => {
    search.query = "from=portal";
    api.billingStatus.mockResolvedValue(free);
    api.syncBilling.mockResolvedValue({ ...free, plan: "free", state: "canceled" });
    render(<BillingPanel />);
    expect(await screen.findByText(/Your Pro plan has ended/)).toBeInTheDocument();
    expect(api.syncBilling).toHaveBeenCalledOnce();
    expect(api.billingStatus).not.toHaveBeenCalled();
  });

  it("shows the stored status when the re-read after the portal fails", async () => {
    search.query = "from=portal";
    api.billingStatus.mockResolvedValue(pro);
    api.syncBilling.mockRejectedValue(new ApiError(429, "RATE_LIMITED", "Slow down.", null));
    render(<BillingPanel />);
    expect(await screen.findByText(/Your Pro plan renews on/)).toBeInTheDocument();
    expect(api.billingStatus).toHaveBeenCalledOnce();
  });

  it("does not re-read Stripe on an ordinary visit", async () => {
    api.billingStatus.mockResolvedValue(free);
    render(<BillingPanel />);
    await screen.findByText(/You are on the Free plan/);
    expect(api.syncBilling).not.toHaveBeenCalled();
  });

  it("re-reads after the portal only at the first load, not when the status is reloaded", async () => {
    search.query = "from=portal";
    api.billingStatus.mockResolvedValue(pro);
    api.syncBilling.mockResolvedValue(pro);
    render(<BillingPanel />);
    await screen.findByText(/Your Pro plan renews on/);
    pageShow(true); // back/forward cache: the status is fetched again, the page URL still has the parameter
    await screen.findByText(/Your Pro plan renews on/);
    expect(api.syncBilling).toHaveBeenCalledOnce();
    expect(api.billingStatus).toHaveBeenCalledOnce();
  });

  it("re-reads Stripe before showing the plan when a refused checkout shows the plan moved (BILL-07)", async () => {
    api.billingStatus.mockResolvedValue(free);
    api.syncBilling.mockResolvedValue(pro);
    api.startCheckout.mockRejectedValue(new ApiError(409, "CONFLICT", "You already have Pro.", null));
    render(<BillingPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Pro — monthly" }));
    await screen.findByRole("alert");
    expect(api.syncBilling).toHaveBeenCalledOnce();
    expect(api.billingStatus).toHaveBeenCalledTimes(2);
  });

  describe("when a new subscription is refused (DEC-013)", () => {
    const blocked = (patch: Partial<Schemas["BillingStatus"]>): Schemas["BillingStatus"] => ({
      ...free,
      available_plans: ["pro_monthly", "pro_yearly"],
      ...patch,
    });

    it("offers the upgrade, and no plan management, when nothing blocks it", async () => {
      api.billingStatus.mockResolvedValue(blocked({}));
      render(<BillingPanel />);
      expect(await screen.findByRole("button", { name: "Pro — monthly" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Pro — yearly" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Manage billing" })).toBeNull();
    });

    it.each([
      [
        "payment_required",
        { state: "payment_required" },
        /cannot be started while this one needs a payment/,
        true,
      ],
      ["subscription_exists", { state: "canceled" }, /already have a subscription/, true],
      ["subscription_exists", { state: "unrecognized" }, /already have a subscription/, true],
      ["subscription_exists", { state: "processing" }, /already have a subscription/, true],
      ["payment_pending", { state: "processing" }, /once this payment is confirmed/, false],
    ] as const)(
      "%s (%j) shows why, never the upgrade, and the way to plan management",
      async (checkoutBlocked, patch, reason, canManage) => {
        api.billingStatus.mockResolvedValue(blocked({ ...patch, checkout_blocked: checkoutBlocked }));
        render(<BillingPanel />);
        expect(await screen.findByText(reason)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Pro — (monthly|yearly)/ })).toBeNull();
        expect(screen.queryByRole("heading", { name: "Upgrade to Pro" })).toBeNull();
        expect(!!screen.queryByRole("button", { name: "Manage billing" })).toBe(canManage);
      },
    );

    it("says a subscription no plan matches was not matched, in the warning card, with plan management", async () => {
      api.billingStatus.mockResolvedValue(
        blocked({ state: "unrecognized", plan_key: "unknown", checkout_blocked: "subscription_exists" }),
      );
      render(<BillingPanel />);
      const copy = await screen.findByText(/could not match your subscription to a plan/);
      expect(copy.closest("section")).toHaveClass("card-warning");
      expect(screen.queryByText(/Your Pro plan has ended/)).toBeNull(); // the user pays: the plan has not ended
      expect(screen.getByRole("button", { name: "Manage billing" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: /Pro — (monthly|yearly)/ })).toBeNull();
    });

    it("for a Pro plan offers plan management, never the upgrade", async () => {
      api.billingStatus.mockResolvedValue(pro);
      render(<BillingPanel />);
      expect(await screen.findByRole("button", { name: "Manage billing" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: /Pro — (monthly|yearly)/ })).toBeNull();
    });

    it("opens the portal from the refusal", async () => {
      api.billingStatus.mockResolvedValue(
        blocked({ state: "payment_required", checkout_blocked: "payment_required" }),
      );
      api.openPortal.mockResolvedValue({ url: "https://billing.stripe.com/p/session/1" });
      const assign = vi.fn();
      vi.spyOn(window, "location", "get").mockReturnValue({
        ...window.location,
        assign,
        origin: window.location.origin,
      });
      render(<BillingPanel />);
      await userEvent.click(await screen.findByRole("button", { name: "Manage billing" }));
      expect(assign).toHaveBeenCalledWith("https://billing.stripe.com/p/session/1");
    });

    it("shows the refusal of a stale page, then the current state with its way on", async () => {
      api.billingStatus
        .mockResolvedValueOnce(blocked({}))
        .mockResolvedValue(blocked({ state: "payment_required", checkout_blocked: "payment_required" }));
      api.startCheckout.mockRejectedValue(
        new ApiError(
          409,
          "CONFLICT",
          "Your subscription needs a payment before you can start a new one.",
          null,
        ),
      );
      render(<BillingPanel />);
      await userEvent.click(await screen.findByRole("button", { name: "Pro — monthly" }));
      expect(await screen.findByRole("button", { name: "Manage billing" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Pro — monthly" })).toBeNull();
      expect(screen.getByRole("alert")).toHaveTextContent("needs a payment");
    });
  });
});
