"use client";

import * as Sentry from "@sentry/nextjs";
import type { ErrorEvent, EventHint } from "@sentry/nextjs";

import { ApiError } from "./api/errors";
import { publicEnv } from "./env";
import { scrubEvent } from "./redact";

// The API assigns IDs of this shape (or accepts the caller's).
const REQUEST_ID = /^[A-Za-z0-9._-]{8,64}$/;

/**
 * `beforeSend` of the browser SDK: the scrubbed report, tagged with the API
 * request ID when it is about a failed API call, so it can be found in the
 * API's logs and error reports, which carry the same tag (OBS-05).
 */
export function prepareReport(event: ErrorEvent, hint?: EventHint): ErrorEvent {
  const scrubbed = scrubEvent(event);
  const failure = hint?.originalException;
  if (failure instanceof ApiError && failure.requestId && REQUEST_ID.test(failure.requestId)) {
    scrubbed.tags = { ...scrubbed.tags, request_id: failure.requestId };
  }
  return scrubbed;
}

export type TestErrorResult = { status: "disabled" } | { status: "sent"; eventId: string; flushed: boolean };

export function errorReportingEnabled(): boolean {
  return publicEnv.errorReporting.enabled;
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
