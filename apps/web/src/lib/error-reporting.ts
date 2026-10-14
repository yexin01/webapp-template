"use client";

import * as Sentry from "@sentry/nextjs";

export type TestErrorResult = { status: "disabled" } | { status: "sent"; eventId: string; flushed: boolean };

export function errorReportingEnabled(): boolean {
  return (
    process.env.NEXT_PUBLIC_ERROR_REPORTING_ENABLED === "true" && Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN)
  );
}

/**
 * Reports a synthetic error through the browser SDK and its scrubbers
 * (integrations page). `flushed` means the SDK drained its queue within the
 * timeout; it does not prove that Sentry stored the event.
 */
export async function sendTestError(): Promise<TestErrorResult> {
  if (!errorReportingEnabled() || !Sentry.getClient()) return { status: "disabled" };
  const eventId = Sentry.captureException(new Error("Synthetic error from the integrations page (web)"), {
    tags: { integration_test: "true" },
  });
  const flushed = await Sentry.flush(3000);
  return { status: "sent", eventId, flushed };
}
