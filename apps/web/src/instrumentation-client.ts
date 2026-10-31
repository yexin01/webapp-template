import * as Sentry from "@sentry/nextjs";

import { publicEnv } from "./lib/env";
import { prepareReport } from "./lib/error-reporting";
import { scrubBreadcrumb, scrubEnvelopeHeaders, scrubSpan, scrubTraceContext } from "./lib/redact";

if (publicEnv.errorReporting.enabled) {
  Sentry.init({
    dsn: publicEnv.errorReporting.dsn,
    environment: publicEnv.appEnv,
    // Collect nothing personal or request-derived; the before* hooks are a second line of defense.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      graphQL: { document: false, variables: false },
      genAI: { inputs: false, outputs: false },
      databaseQueryData: false,
      queues: false,
      stackFrameVariables: false,
    },
    // No tracesSampleRate or tracesSampler, on purpose: the browser records no spans, but its same-origin
    // /api/v1 requests still carry sentry-trace and baggage without a sampling decision, so the API samples
    // them at SENTRY_TRACES_SAMPLE_RATE and browser errors share the API trace ID. Any defined rate, even 0,
    // would decide for the API ("not sampled") and still record spans under a sampled server-rendered page.
    // No tracePropagationTargets either: the browser's default is the page's own origin, so no other host gets
    // a trace header.
    // Session replay is not enabled; it would require an approved privacy review.
    beforeSend: prepareReport,
    beforeBreadcrumb: scrubBreadcrumb,
    // The browser records no spans; this keeps URLs out of them if tracing is ever turned on.
    beforeSendSpan: scrubSpan,
  });
  // The trace context still travels in envelope headers and in the baggage header of /api/v1 requests.
  const client = Sentry.getClient();
  client?.on("createDsc", scrubTraceContext);
  client?.on("beforeEnvelope", scrubEnvelopeHeaders);
}

// Sentry's App Router navigation hook. It does nothing while Sentry is disabled: no handler is registered.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
