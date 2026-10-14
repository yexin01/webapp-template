import { render, screen } from "@testing-library/react";

import type { Schemas } from "@/lib/api/client";

import { describeStatus } from "./billing-panel";
import { Paywall } from "./paywall";
import { PlanBadge } from "./plan-badge";

const base: Schemas["BillingStatus"] = {
  plan: "free",
  state: "none",
  plan_key: null,
  current_period_end: null,
  cancels_at: null,
  available_plans: ["pro_monthly", "pro_yearly"],
};

describe("billing status copy", () => {
  it.each([
    [{ state: "none" }, "Free plan"],
    [{ plan: "pro", state: "active", current_period_end: "2026-07-01T00:00:00Z" }, "renews on"],
    [{ plan: "pro", state: "canceling", cancels_at: "2026-07-01T00:00:00Z" }, "ends on"],
    [{ plan: "pro", state: "past_due" }, "payment failed"],
    [{ state: "processing" }, "being confirmed"],
    [{ state: "payment_required" }, "Payment is required"],
    [{ state: "canceled" }, "has ended"],
  ] as const)("%o", (patch, text) => {
    expect(describeStatus({ ...base, ...patch } as Schemas["BillingStatus"])).toContain(text);
  });
});

describe("paywall", () => {
  it("links to plans in personal context", () => {
    render(<Paywall feature="pro_insight" />);
    expect(screen.getByRole("heading", { name: "Upgrade to Pro" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/billing");
  });

  it("explains in organizations that Pro is personal and not shared, with an upgrade link", () => {
    render(<Paywall feature="pro_insight" inOrganization />);
    expect(screen.getByText(/your own Pro plan/)).toBeInTheDocument();
    expect(screen.getByText(/not shared with other members/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/billing");
  });
});

describe("plan badge", () => {
  it("has an accessible label", () => {
    render(<PlanBadge plan="pro" />);
    expect(screen.getByText("Current plan:")).toHaveClass("visually-hidden");
  });
});
