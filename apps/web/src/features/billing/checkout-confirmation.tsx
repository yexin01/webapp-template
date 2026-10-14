"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useApi } from "@/lib/api/use-api";
import { LoadingState } from "@/ui/state-views";

const ATTEMPTS = 20;
const INTERVAL_MS = 1500;

/**
 * After Stripe redirects back, Pro is granted only once the server has
 * synchronized the subscription from a verified webhook (BILL-06). This page
 * polls the server; it never trusts the redirect itself.
 */
export function CheckoutConfirmation() {
  const api = useApi();
  const [state, setState] = useState<"waiting" | "confirmed" | "delayed">("waiting");

  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    async function poll() {
      attempt += 1;
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
      if (attempt >= ATTEMPTS) setState("delayed");
      else window.setTimeout(() => void poll(), INTERVAL_MS);
    }
    void poll();
    return () => {
      cancelled = true;
    };
  }, [api]);

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
