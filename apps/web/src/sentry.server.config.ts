import * as Sentry from "@sentry/nextjs";

import { loadServerEnv } from "./lib/env.server";
import { scrubBreadcrumb, scrubEvent } from "./lib/redact";

// Validated configuration (ENV-03). Error reporting needs no Clerk secret, so
// this check does not repeat the serving requirements next.config.ts enforces.
const { appEnv, errorReporting } = loadServerEnv(process.env, { serving: false });

if (errorReporting.enabled) {
  Sentry.init({
    dsn: errorReporting.dsn,
    environment: appEnv,
    // Collect nothing personal or request-derived; beforeSend is a second line of defense.
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
    beforeSend: scrubEvent,
    beforeBreadcrumb: scrubBreadcrumb,
  });
}
