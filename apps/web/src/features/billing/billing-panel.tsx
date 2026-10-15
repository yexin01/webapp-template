"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { track } from "@/lib/analytics";
import { isAllowedRedirect, type Schemas } from "@/lib/api/client";
import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";
import { ErrorState, LoadingState } from "@/ui/state-views";

import { PlanBadge } from "./plan-badge";

type Status = Schemas["BillingStatus"];
type PlanKey = Schemas["CheckoutRequest"]["plan"];

const PLAN_LABELS: Record<PlanKey, string> = { pro_monthly: "Pro — monthly", pro_yearly: "Pro — yearly" };

function formatDate(value: string | null): string | null {
  return value ? new Date(value).toLocaleDateString(undefined, { dateStyle: "long" }) : null;
}

/** Copy for each server-provided billing state (BILL-20). */
export function describeStatus(status: Status): string {
  const end = formatDate(status.current_period_end);
  switch (status.state) {
    case "active":
      return end ? `Your Pro plan renews on ${end}.` : "Your Pro plan is active.";
    case "canceling":
      return `Your Pro plan is canceled and ends on ${formatDate(status.cancels_at) ?? end ?? "the end of the period"}.`;
    case "past_due":
      return "Your last payment failed. Update your payment method to keep Pro.";
    case "processing":
      return "Your payment is being confirmed.";
    case "payment_required":
      return "Payment is required to restore Pro.";
    case "canceled":
      return "Your Pro plan has ended. You are on the Free plan.";
    default:
      return "You are on the Free plan.";
  }
}

export function BillingPanel() {
  const api = useApi();
  const params = useSearchParams();
  const [actionError, setActionError] = useState<unknown>(null);
  const [pending, setPending] = useState<string | null>(null);

  const fetchStatus = useCallback(() => api.billingStatus(), [api]);
  const { data: status, error, reload } = useResource(fetchStatus, "billing-status");

  // Back from Stripe, the browser may restore this page from its back/forward
  // cache exactly as it was left: mid-redirect, with every button disabled.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setPending(null);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  async function go(action: "portal" | PlanKey) {
    setPending(action);
    setActionError(null);
    try {
      let url: string;
      if (action === "portal") {
        track("billing_portal_clicked");
        url = (await api.openPortal()).url;
      } else {
        track("upgrade_clicked", { plan: action });
        url = (await api.startCheckout(action, crypto.randomUUID())).url;
      }
      if (!isAllowedRedirect(url, window.location.origin)) throw new Error("Unexpected redirect target");
      window.location.assign(url);
    } catch (e) {
      setActionError(e);
      setPending(null);
    }
  }

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!status) return <LoadingState label="Loading billing…" />;

  const needsAttention = status.state === "past_due" || status.state === "payment_required";

  return (
    <div className="stack">
      <div className="page-heading">
        <h1>Billing</h1>
        <PlanBadge plan={status.plan} />
      </div>
      {params.get("checkout") === "canceled" && (
        <p className="notice" role="status">
          Checkout was canceled. You have not been charged.
        </p>
      )}
      <section className={`card${needsAttention ? " card-warning" : ""}`} aria-labelledby="plan-title">
        <h2 id="plan-title">Current plan</h2>
        <p>{describeStatus(status)}</p>
        {(status.plan === "pro" || needsAttention || status.state === "canceled") && (
          <button
            type="button"
            className="button"
            disabled={pending !== null}
            onClick={() => void go("portal")}
          >
            {pending === "portal" ? "Opening…" : "Manage billing"}
          </button>
        )}
      </section>

      {status.plan === "free" && status.state !== "processing" && (
        <section className="card" aria-labelledby="upgrade-title">
          <h2 id="upgrade-title">Upgrade to Pro</h2>
          {status.available_plans.length === 0 ? (
            <p className="muted">Paid plans are not configured in this environment.</p>
          ) : (
            <div className="button-row">
              {status.available_plans.map((plan) => (
                <button
                  key={plan}
                  type="button"
                  className="button button-primary"
                  disabled={pending !== null}
                  onClick={() => void go(plan)}
                >
                  {pending === plan ? "Redirecting…" : PLAN_LABELS[plan]}
                </button>
              ))}
            </div>
          )}
        </section>
      )}
      {actionError !== null && <ErrorState error={actionError} />}
    </div>
  );
}
