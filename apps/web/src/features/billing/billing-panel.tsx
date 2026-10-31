"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { track } from "@/lib/analytics";
import { isAllowedRedirect, type Schemas } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
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
    case "unrecognized":
      return "We could not match your subscription to a plan, so Pro is not active. Use Manage billing to see it, or contact support.";
    case "canceled":
      return "Your Pro plan has ended. You are on the Free plan.";
    default:
      return "You are on the Free plan.";
  }
}

/**
 * Why a new subscription cannot be started (DEC-013), with the way on. Null when it can.
 * Plan management is where such a subscription is fixed, changed, or ended.
 */
export function describeCheckoutBlock(status: Status): string | null {
  switch (status.checkout_blocked) {
    case "payment_required":
      return "A new subscription cannot be started while this one needs a payment. Use Manage billing to update your payment method or settle the invoice.";
    case "subscription_exists":
      return "You already have a subscription. Use Manage billing to change or cancel it instead of starting another.";
    case "payment_pending":
      return "You can start a new subscription once this payment is confirmed.";
    default:
      return null;
  }
}

export function BillingPanel() {
  const api = useApi();
  const params = useSearchParams();
  const [actionError, setActionError] = useState<unknown>(null);
  const [pending, setPending] = useState<string | null>(null);

  // Back from the Customer Portal the webhook may not have landed yet: the first load asks the server
  // to re-read Stripe. The parameter only triggers that read; it never grants or shows anything itself.
  const syncFirst = useRef(params.get("from") === "portal");
  const fetchStatus = useCallback(async () => {
    if (syncFirst.current) {
      syncFirst.current = false;
      try {
        return await api.syncBilling();
      } catch {
        /* show the stored status instead */
      }
    }
    return api.billingStatus();
  }, [api]);
  const { data: status, error, reload } = useResource(fetchStatus, "billing-status");

  // Back from Stripe, the browser may restore this page from its back/forward
  // cache exactly as it was left: mid-redirect, with every button disabled, and
  // with the status from before checkout or the portal changed it. The status
  // is fetched again, and shown as loading until it arrives (BILL-20).
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      setPending(null);
      reload();
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, [reload]);

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
      // The plan changed since this page loaded (BILL-07, DEC-013): re-read Stripe, since the webhook
      // may not have landed, then show the current plan and its plan management.
      if (e instanceof ApiError && e.code === "CONFLICT") {
        void api
          .syncBilling()
          .catch(() => undefined)
          .finally(reload);
      }
    }
  }

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!status) return <LoadingState label="Loading billing…" />;

  const needsAttention =
    status.state === "past_due" || status.state === "payment_required" || status.state === "unrecognized";
  const blockCopy = describeCheckoutBlock(status);
  // Plan management is the way on for every refusal except a payment that is still being confirmed.
  const canManage =
    status.plan === "pro" ||
    needsAttention ||
    status.state === "canceled" ||
    (status.checkout_blocked !== null && status.checkout_blocked !== "payment_pending");

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
        {blockCopy !== null && <p className="muted">{blockCopy}</p>}
        {canManage && (
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

      {status.checkout_blocked === null && (
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
