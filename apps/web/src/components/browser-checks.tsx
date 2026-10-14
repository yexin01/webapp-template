"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import { useState } from "react";

import { analyticsEnabled, sendTestEvent } from "@/lib/analytics";
import type { Schemas } from "@/lib/api/client";
import { errorReportingEnabled, sendTestError } from "@/lib/error-reporting";

import { ModeBadge, OutcomeMessage } from "./integration-status";

type Mode = Schemas["IntegrationMode"];
type Outcome = { outcome: Schemas["Outcome"]; message: string; reference?: string };

function clerkMode(): Mode {
  const key = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
  if (key.startsWith("pk_live_")) return "live";
  return key.startsWith("pk_test_") ? "sandbox" : "disabled";
}

/** Browser-side SDKs: the Clerk session, PostHog, and Sentry as configured for the web app. */
export function BrowserChecks({ environment }: { environment: string }) {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  const { organization } = useOrganization();
  const telemetryMode: Mode = environment === "production" ? "live" : "sandbox";
  const [analytics, setAnalytics] = useState<Outcome | null>(null);
  const [errors, setErrors] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  function runAnalytics() {
    const status = sendTestEvent();
    setAnalytics(
      status === "sent"
        ? {
            outcome: "sent",
            message:
              "Sent integration_test_sent from the browser. The browser SDK does not report delivery: confirm it under Activity in PostHog.",
          }
        : { outcome: "skipped", message: "Browser analytics is disabled (ANALYTICS_MODE)." },
    );
  }

  async function runErrors() {
    setBusy(true);
    const result = await sendTestError();
    setErrors(
      result.status === "sent"
        ? {
            outcome: result.flushed ? "sent" : "failed",
            message: result.flushed
              ? "Sent a synthetic browser error. The SDK does not confirm delivery: find the event ID in Sentry and confirm that no personal data was captured."
              : "The error was queued, but the SDK could not send it within 3 seconds.",
            reference: result.eventId,
          }
        : { outcome: "skipped", message: "Browser error reporting is disabled (ERROR_REPORTING_MODE)." },
    );
    setBusy(false);
  }

  return (
    <section aria-labelledby="browser-checks-title">
      <h2 id="browser-checks-title">Browser checks</h2>
      <ul className="integration-grid">
        <li className="card integration-card">
          <div className="integration-card-header">
            <h3>Clerk session</h3>
            <ModeBadge mode={clerkMode()} />
          </div>
          <ul className="detail-list">
            <li>
              {!isLoaded ? "Loading session…" : isSignedIn ? "Signed in; session active" : "Signed out"}
            </li>
            <li>Active workspace: {organization ? organization.name : "Personal"}</li>
            {sessionId && <li>Tokens are sent to the API as a bearer header, never as cookies</li>}
          </ul>
          <p className="muted check-meta">Shown live from the Clerk SDK; no check is needed.</p>
        </li>

        <li className="card integration-card">
          <div className="integration-card-header">
            <h3>PostHog (browser)</h3>
            <ModeBadge mode={analyticsEnabled() ? telemetryMode : "disabled"} />
          </div>
          <ul className="detail-list">
            <li>Autocapture and session recording are off</li>
            <li>Text and attributes are masked; query strings are stripped</li>
          </ul>
          <button type="button" className="button button-small" onClick={runAnalytics}>
            Send test event
          </button>
          <div aria-live="polite">
            {analytics && <OutcomeMessage outcome={analytics.outcome}>{analytics.message}</OutcomeMessage>}
          </div>
        </li>

        <li className="card integration-card">
          <div className="integration-card-header">
            <h3>Sentry (browser)</h3>
            <ModeBadge mode={errorReportingEnabled() ? telemetryMode : "disabled"} />
          </div>
          <ul className="detail-list">
            <li>User data, headers, cookies, and query strings are not collected</li>
            <li>Session replay is not enabled</li>
          </ul>
          <button
            type="button"
            className="button button-small"
            disabled={busy}
            onClick={() => void runErrors()}
          >
            {busy ? "Sending…" : "Send test error"}
          </button>
          <div aria-live="polite">
            {errors && (
              <OutcomeMessage outcome={errors.outcome}>
                <p>{errors.message}</p>
                {errors.reference && (
                  <p className="muted check-meta">
                    reference <code>{errors.reference}</code>
                  </p>
                )}
              </OutcomeMessage>
            )}
          </div>
        </li>
      </ul>
    </section>
  );
}
