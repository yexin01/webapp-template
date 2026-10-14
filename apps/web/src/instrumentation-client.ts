import * as Sentry from "@sentry/nextjs";

import { publicEnv } from "./lib/env";
import { redactText, stripQuery } from "./lib/redact";

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
    beforeSend(event) {
      delete event.user;
      if (event.request?.url) event.request.url = stripQuery(event.request.url);
      if (event.request) {
        delete event.request.headers;
        delete event.request.cookies;
        delete event.request.query_string;
      }
      for (const exception of event.exception?.values ?? []) {
        if (exception.value) exception.value = redactText(exception.value);
      }
      return event;
    },
    beforeBreadcrumb(breadcrumb) {
      if (breadcrumb.category === "ui.input") return null;
      if (typeof breadcrumb.data?.url === "string") breadcrumb.data.url = stripQuery(breadcrumb.data.url);
      if (breadcrumb.message) breadcrumb.message = redactText(breadcrumb.message);
      return breadcrumb;
    },
  });
}
