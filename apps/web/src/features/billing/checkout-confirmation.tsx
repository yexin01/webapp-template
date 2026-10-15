"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useApi } from "@/lib/api/use-api";
import { LoadingState } from "@/ui/state-views";

const INTERVAL_MS = 1500;
/**
 * How long the page waits for confirmation, by the clock from page load: a
 * slow or hanging API (each request may take up to its own deadline), or a
 * Clerk that loads late or never, cannot stretch it.
 */
const BUDGET_MS = 30_000;

type View = "waiting" | "confirmed" | "delayed";

/**
 * After Stripe redirects back, Pro is granted only once the server has
 * synchronized the subscription from a verified webhook (BILL-06). This page
 * polls the server; it never trusts the redirect itself.
 */
export function CheckoutConfirmation() {
  const { isLoaded, userId } = useAuth();
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    const giveUp = window.setTimeout(() => setExpired(true), BUDGET_MS);
    return () => window.clearTimeout(giveUp);
  }, []);

  return (
    <section className="card" aria-live="polite">
      <h1>Confirming your subscription</h1>
      {/* Polling starts once Clerk has loaded: the client changes when the user
          arrives, and starting earlier would run a second chain. It restarts per
          user, so an account switch never shows the previous user's result. */}
      {isLoaded ? (
        <Confirmation key={userId ?? "signed-out"} expired={expired} />
      ) : (
        <Outcome view={expired ? "delayed" : "waiting"} />
      )}
    </section>
  );
}

function Confirmation({ expired }: { expired: boolean }) {
  const api = useApi();
  const [state, setState] = useState<View>("waiting");

  useEffect(() => {
    // One polling chain per effect run. Cleanup (navigation, or StrictMode's
    // remount) stops it on every path: no state update and no further request.
    let cancelled = false;
    let timer: number | undefined;
    const deadline = Date.now() + BUDGET_MS;
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
    };
  }, [api]);

  // The page gives up on time even while a request is still pending; a late "pro" answer still confirms.
  return <Outcome view={state === "waiting" && expired ? "delayed" : state} />;
}

function Outcome({ view }: { view: View }) {
  if (view === "waiting") return <LoadingState label="Waiting for payment confirmation…" />;
  if (view === "confirmed") {
    return (
      <>
        <p className="notice notice-success">You are now on Pro. Thank you!</p>
        <Link className="button button-primary" href="/dashboard">
          Go to dashboard
        </Link>
      </>
    );
  }
  return (
    <>
      <p>
        Confirmation is taking longer than usual. If your payment succeeded, Pro will appear shortly — you do
        not need to pay again.
      </p>
      <div className="button-row">
        <Link className="button" href="/billing">
          Check billing status
        </Link>
        <button type="button" className="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    </>
  );
}
