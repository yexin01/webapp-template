import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/lib/api/client";

import { BillingPanel } from "./billing-panel";

/**
 * SEC-14 in the browser: the API has already checked the host of a Checkout or Customer Portal URL, and the
 * panel checks it again before leaving (isAllowedRedirect). A URL outside the allowlist is never followed.
 */
const api = vi.hoisted(() => ({
  billingStatus: vi.fn(),
  startCheckout: vi.fn(),
  openPortal: vi.fn(),
  syncBilling: vi.fn(),
}));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("@/lib/analytics", () => ({ track: vi.fn() }));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ isLoaded: true, userId: "user_1" }),
  useClerk: () => ({ status: "ready" }),
}));
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
  checkout_blocked: null,
};
const pro: Schemas["BillingStatus"] = {
  ...free,
  plan: "pro",
  state: "active",
  plan_key: "pro_monthly",
  current_period_end: "2026-11-15T00:00:00Z",
  checkout_blocked: "already_pro",
};

const assign = vi.fn<(url: string | URL) => void>();

beforeEach(() => {
  for (const method of Object.values(api)) method.mockReset();
  vi.spyOn(window, "location", "get").mockReturnValue({
    ...window.location,
    assign,
    origin: window.location.origin,
  });
});
afterEach(() => vi.restoreAllMocks());

describe("billing panel redirects (SEC-14)", () => {
  it.each([
    ["Checkout", "https://checkout.stripe.com/c/pay/cs_test_1", "Pro — monthly"],
    ["the Customer Portal", "https://billing.stripe.com/p/session/1", "Manage billing"],
  ])("follows a Stripe URL for %s", async (_what, url, button) => {
    api.billingStatus.mockResolvedValue(button === "Manage billing" ? pro : free);
    api.startCheckout.mockResolvedValue({ url });
    api.openPortal.mockResolvedValue({ url });
    render(<BillingPanel />);
    await userEvent.click(await screen.findByRole("button", { name: button }));
    expect(assign).toHaveBeenCalledExactlyOnceWith(url);
  });

  it.each([
    ["Checkout", "https://evil.example/pay", "Pro — monthly"],
    ["Checkout", "https://evilstripe.com/c/pay/cs_test_1", "Pro — monthly"],
    ["the Customer Portal", "http://billing.stripe.com/p/session/1", "Manage billing"],
    ["the Customer Portal", "javascript:alert(1)", "Manage billing"],
  ])("does not follow a URL for %s that is off the allowlist: %s", async (_what, url, button) => {
    api.billingStatus.mockResolvedValue(button === "Manage billing" ? pro : free);
    api.startCheckout.mockResolvedValue({ url });
    api.openPortal.mockResolvedValue({ url });
    render(<BillingPanel />);
    await userEvent.click(await screen.findByRole("button", { name: button }));

    const alert = await screen.findByRole("alert");
    expect(assign, "the browser was sent to a URL outside the allowlist").not.toHaveBeenCalled();
    // The refusal is the generic error: neither the URL nor why it was refused is shown.
    expect(alert).toHaveTextContent("Something went wrong");
    expect(alert.textContent).not.toMatch(/evil|stripe|javascript|http/i);
    // The panel is usable again, not left on "Redirecting…".
    expect(screen.getByRole("button", { name: button })).toBeEnabled();
  });
});
