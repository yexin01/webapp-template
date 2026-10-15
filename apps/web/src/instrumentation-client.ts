import * as Sentry from "@sentry/nextjs";

import { publicEnv } from "./lib/env";
import { scrubBreadcrumb, scrubEvent } from "./lib/redact";

if (publicEnv.errorReporting.enabled) {
  Sentry.init({
    dsn: publicEnv.errorReporting.dsn,
    environment: publicEnv.appEnv,
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
    tracesSampleRate: 0,
    // Session replay is not enabled; it would require an approved privacy review.
    beforeSend: scrubEvent,
    beforeBreadcrumb: scrubBreadcrumb,
  });
}
