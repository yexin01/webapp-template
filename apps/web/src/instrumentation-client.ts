import * as Sentry from "@sentry/nextjs";

import { publicEnv } from "./lib/env";
import {
  scrubBreadcrumb,
  scrubEnvelopeHeaders,
  scrubEvent,
  scrubSpan,
  scrubTraceContext,
} from "./lib/redact";

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
    tracesSampleRate: 0,
    // Session replay is not enabled; it would require an approved privacy review.
    beforeSend: scrubEvent,
    beforeBreadcrumb: scrubBreadcrumb,
    // Tracing is off here; this keeps URLs out of spans if it is ever turned on.
    beforeSendSpan: scrubSpan,
  });
  // The trace context still travels in envelope headers and in the baggage header of /api/v1 requests.
  const client = Sentry.getClient();
  client?.on("createDsc", scrubTraceContext);
  client?.on("beforeEnvelope", scrubEnvelopeHeaders);
}
