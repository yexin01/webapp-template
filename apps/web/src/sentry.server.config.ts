import * as Sentry from "@sentry/nextjs";

import { redactText, stripQuery } from "./lib/redact";

const enabled = process.env.ERROR_REPORTING_MODE === "sandbox" || process.env.ERROR_REPORTING_MODE === "live";

if (enabled && process.env.NEXT_PUBLIC_SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    environment: process.env.APP_ENV ?? "local",
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
    tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE ?? 0),
    beforeSend(event) {
      if (event.request) {
        delete event.request.cookies;
        delete event.request.headers;
        delete event.request.data;
        if (event.request.url) event.request.url = stripQuery(event.request.url);
        delete event.request.query_string;
      }
      delete event.user;
      for (const exception of event.exception?.values ?? []) {
        if (exception.value) exception.value = redactText(exception.value);
      }
      if (event.message) event.message = redactText(event.message);
      return event;
    },
  });
}
