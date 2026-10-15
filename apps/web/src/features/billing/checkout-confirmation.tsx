"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useApi } from "@/lib/api/use-api";
import { LoadingState } from "@/ui/state-views";

const INTERVAL_MS = 1500;
/**
 * How long the page waits for confirmation, by the clock: a slow or hanging
 * API (each request may take up to its own deadline) cannot stretch it.
 */
const BUDGET_MS = 30_000;

/**
 * After Stripe redirects back, Pro is granted only once the server has
 * synchronized the subscription from a verified webhook (BILL-06). This page
 * polls the server; it never trusts the redirect itself.
 */
export function CheckoutConfirmation() {
  const api = useApi();
  const { isLoaded } = useAuth();
  const [state, setState] = useState<"waiting" | "confirmed" | "delayed">("waiting");

  useEffect(() => {
    // Polling starts once Clerk has loaded: the client changes when the user
    // arrives, and starting earlier would run a second chain.
    if (!isLoaded) return;
    // One polling chain per effect run. Cleanup (navigation, or StrictMode's
    // remount) stops it on every path: no state update and no further request.
    let cancelled = false;
    let timer: number | undefined;
    const deadline = Date.now() + BUDGET_MS;
    // Gives up on time even while a request is still pending; a late "pro" answer still confirms.
    const giveUp = window.setTimeout(() => setState((s) => (s === "waiting" ? "delayed" : s)), BUDGET_MS);
    async function poll() {
      try {
        const status = await api.billingStatus();
        if (cancelled) return;
        if (status.plan === "pro") {
          setState("confirmed");
          return;
        }
      } catch {
        /* keep polling; transient failures are expected here */
      }
      if (cancelled) return;
      if (Date.now() + INTERVAL_MS >= deadline) setState("delayed");
      else timer = window.setTimeout(() => void poll(), INTERVAL_MS);
    }
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.clearTimeout(giveUp);
    };
  }, [api, isLoaded]);

  return (
    <section className="card" aria-live="polite">
      <h1>Confirming your subscription</h1>
      {state === "waiting" && <LoadingState label="Waiting for payment confirmation…" />}
      {state === "confirmed" && (
        <>
          <p className="notice notice-success">You are now on Pro. Thank you!</p>
          <Link className="button button-primary" href="/dashboard">
            Go to dashboard
          </Link>
        </>
      )}
      {state === "delayed" && (
        <>
          <p>
            Confirmation is taking longer than usual. If your payment succeeded, Pro will appear shortly — you
            do not need to pay again.
          </p>
          <Link className="button" href="/billing">
            Check billing status
          </Link>
        </>
      )}
    </section>
  );
}
