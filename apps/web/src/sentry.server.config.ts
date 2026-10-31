import * as Sentry from "@sentry/nextjs";

import { loadServerEnv } from "./lib/env.server";
import {
  scrubBreadcrumb,
  scrubEnvelopeHeaders,
  scrubEvent,
  scrubSpan,
  scrubTraceContext,
} from "./lib/redact";

// Validated configuration (ENV-03). Error reporting needs no Clerk secret, so
// this check does not repeat the serving requirements next.config.ts enforces.
const { appEnv, errorReporting } = loadServerEnv(process.env, { serving: false });

if (errorReporting.enabled) {
  Sentry.init({
    dsn: errorReporting.dsn,
    environment: appEnv,
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
    tracesSampleRate: errorReporting.tracesSampleRate,
    // Without a list the SDK adds sentry-trace and baggage (release, environment, trace ID, route names) to every
    // request the server makes, whoever receives it. With none, a request keeps only the headers it already has.
    tracePropagationTargets: [],
    beforeSend: scrubEvent,
    beforeBreadcrumb: scrubBreadcrumb,
    // Spans never reach beforeSend; Next.js puts the raw request URL on them.
    beforeSendSpan: scrubSpan,
  });
  // The trace context in envelope and baggage headers names the root span; no option above sees it.
  // From getClient(): the edge runtime's init returns nothing.
  const client = Sentry.getClient();
  client?.on("createDsc", scrubTraceContext);
  client?.on("beforeEnvelope", scrubEnvelopeHeaders);
}
