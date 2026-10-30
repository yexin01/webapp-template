# Package `obs`: observability

Branch `arch/obs`, on top of `arch/apix` (its `ErrorThrottle` and `report_server_error` are used as they are). Web and
API. `README`, `AGENTS.md`, `docs/`, and `dev-docs/` are untouched; the documentation changes are listed for the
integrator.

## What changed

### Browser trace sampling (`obs-browser-zero-rate-unsamples-api`)

Confirmed on the current code with the real browser SDK build (`@sentry/browser` 11.7.0 in jsdom, `fetch` stubbed): a
`tracesSampleRate` of 0 sends `sentry-trace: <trace>-<span>-0` and `baggage: ...sentry-sampled=false,...sample_rate=0`
with every same-origin `/api/v1` request; with no rate it sends `<trace>-<span>` and no flag. sentry-sdk on the API
inherits a caller's decision, so `SENTRY_TRACES_SAMPLE_RATE` never traced a request made from a page. The base's
hardening (rate 0 on the API keeps a caller's trace ID and never traces) did not change this: it concerns the API's own
rate. Fixed:

- `instrumentation-client.ts` sets neither `tracesSampleRate` nor `tracesSampler`. The browser records no spans, sends
  no decision, and the API samples at `SENTRY_TRACES_SAMPLE_RATE`; browser errors still share the API's trace ID.
  `beforeSendSpan` and the `createDsc`/`beforeEnvelope` hooks stay. It also exports
  `onRouterTransitionStart = Sentry.captureRouterTransitionStart` (Sentry's App Router hook; a no-op while Sentry is
  disabled).
- `sentry-config.test.ts`: the options have no `tracesSampleRate`, `tracesSampler`, or `tracePropagationTargets`; the
  navigation hook is the SDK's. `test_sentry_tracing.py`: the API samples a request with no trace or no decision at its
  rate and inherits "not sampled" (`-0`).
- `.env.example`: one sentence on `SENTRY_TRACES_SAMPLE_RATE`.

### Web analytics facade (`obs-web-analytics-facade-drops-and-bypass`)

- `track()` kept nothing before PostHog started (after `/me`). Now it keeps at most 20 events (newest dropped past that,
  OBS-06) with the time they happened. `flushPendingEvents()` sends them with `posthog.capture(event, props,
  {timestamp})` right after `identify(...)`, and after the signed-out and `/me`-failed branches.
  `discardPendingEvents()` drops them: the provider calls it when the Clerk user changed before start, so one user's
  events are never sent as the next; a consent-denied path (DEC-004) should call it too. While analytics is disabled
  nothing is kept.
- `before_send` now enforces the allowlist for everything the SDK sends (`allowlistEvent`): an event that is not in
  `ANALYTICS_EVENTS` or `SDK_EVENTS` (`$pageview`, `$identify`) goes nowhere. Of the rest only these stay: the event's
  allowlisted properties (strings only), properties starting with `$` (the SDK's own) except `$set`/`$set_once`/`$unset`,
  `distinct_id`, and `token`. Top-level `$set` and `$unset` are dropped; `$set_once` keeps its `$`-prefixed entries
  (`$initial_*`). So `register()`, `identify(id, {email})`, `setPersonProperties()`, `group()`, `alias()`, and capture
  options cannot carry personal data. Real-SDK tests: `analytics-pending.test.tsx`, `analytics-direct-capture.test.tsx`.
- Lint: `eslint.guards.mjs` bans `posthog-js` (static, `import()`, `require()`) outside `src/lib/analytics.ts` and the
  `src/lib/analytics*.test.*` files. Tested through `guardConfigs()` in `boundaries.test.ts`; it takes effect in
  `pnpm lint` with the already pending step (spec §23 step 4: spread `guardConfigs()` into `eslint.config.mjs`). I ran
  the guards over all of `src/` with `guardConfigs()`: no violation.
- Refuted/modified from the briefing: the paywall race is a race (as the verifier said); nothing else.

### Telemetry flush on a serverless instance (`obs-api-telemetry-not-flushed-serverless`)

- A request that queued an error event (`scrub_event` marks the request) or an analytics event
  (`PostHogAnalytics._queue` marks it) hands `flush_telemetry` to a thread after its response, or its 500, has been
  sent: `sentry_sdk.flush(timeout)` then `analytics.flush()`, each failure logged and swallowed. The request waits at
  most `TELEMETRY_FLUSH_SECONDS` (2 s, `kernel/http/middleware.py`) and then returns (`telemetry_flush_incomplete`).
  It never changes the response. A request that queued nothing makes no thread hop.
- Single flight: a thread whose wait was given up on keeps running (anyio releases the limiter token when a thread is
  abandoned), so the next flush finds the lock taken and returns at once (`telemetry_flush_skipped`); an outage cannot
  pile up threads. The flush thread has its own limiter, outside the 40 request threads, so the request-thread budget
  (`reserved_request_threads`) is unchanged.
- The outbox handlers' confirmation (`capture(event_id=...)` sends on its own, `flush()` confirms it) is untouched and
  does not mark the request.
- Not covered: the sampled transaction of the request. It is enqueued when Sentry's ASGI middleware, outside ours,
  finishes, after our flush. With `SENTRY_TRACES_SAMPLE_RATE` above 0 on a platform that freezes the instance, some
  transactions can be lost; errors are not.
- Proof of the wire behaviour is in the wire harness below (a 500, a 503, a logged error, and a capture had reached the
  loopback server by the time the client had its answer). That is `mocked` evidence. Whether Vercel keeps the instance
  alive long enough after the response is `NOT VERIFIED` (see the checklist item below).

### Error context (`obs-no-module-owned-events-or-error-context`)

- OBS-04 (the real defect): every request has a mutable `RequestTelemetry` (`kernel/request_context.py`: request ID,
  `user_id`, `org_id`). The identity module's `personal()` and `organization()` dependencies call `bind_tenant` once the
  tenant context is resolved (internal UUIDs only, never the Clerk IDs). `JsonFormatter` writes `user_id`/`org_id` on
  every line of the request (a log line's own `extra` keys win), and Sentry error events get the same two tags next to
  `request_id`. The Sentry `user` field stays removed. A request refused before it has a tenant context (401, a
  non-member organization) logs neither. The holder is mutable because the auth dependencies are synchronous and run in a
  copy of the request's context.
- OBS-05 (web): `ErrorState` shows `Reference: <request ID>` for a 5xx `ApiError` (what `docs/operations.md` starts an
  incident from); `prepareReport` (`lib/error-reporting.ts`, the browser's `beforeSend`) tags a report about a failed API
  call with `request_id`, the tag the API's own reports carry (validated against the API's ID shape).
- Analytics events by module: `Contribution.analytics_events` (a mapping of event name to its allowed properties).
  The composition root registers every contribution's events in the container's `AnalyticsEvents` (the kernel's
  `ANALYTICS_ALLOWLIST` plus the modules'), which `PostHogAnalytics` and `NoopAnalytics` filter by
  (`Kernel.analytics_events`). Registration refuses (and stops startup for) a name another event has, a property named
  like personal data or a secret (the redaction list: `email`, `token`, `prompt`, ...), and a name that is not lower-case
  snake case. The registry belongs to the container; nothing is shared between containers or tests. The
  `RecordingAnalytics` test fake filters by the container's registry too. No generator template change.

### Wire harness (`testing-telemetry-wire-harness`)

`tests/telemetry_child.py` runs `bootstrap(settings)` (now accepts optional settings) in its own process and sends
requests with canary secrets, emails, a JWT, and prompt text in query strings, headers, a path segment, a JSON body,
exception messages, log arguments and `extra`, the Sentry scope (user, tags, extras, contexts, breadcrumbs), an outbound
httpx call (span and breadcrumb), a 500, a 503 the API chose, and PostHog captures. `tests/integration/test_telemetry_wire.py`
decodes everything a loopback stub received (envelopes, gzip, PostHog batches), searches bodies, headers, and paths for
each canary in raw, URL-encoded, and JSON-escaped form, and scans the child's log output. It also checks the run is not
vacuous. The stub for the provider the app calls is separate, because that call legitimately carries its query.
Runtime: about 2 s. The Playwright `telemetry.spec.ts` of the briefing is not written (no account-free browser tier yet);
the web side keeps `sentry-canary.test.ts` and `analytics-canary.test.ts`.

### `lib/api/errors.ts` (follow-up of `apix`)

An error code the web does not know: a 4xx shows the server's message ("Could not complete this", no action), a 503
offers a retry with the server's message; `HTTP_ERROR` (a response outside the contract) and other 5xx keep the generic
copy and never echo the body. `client.test.ts` covers it.

## New commands and environment variables

None. `SENTRY_TRACES_SAMPLE_RATE` (existing) now also decides what is traced of the requests pages make.
`TELEMETRY_FLUSH_SECONDS` is a code constant. No migration, no contract change (`pnpm contracts:check` unaffected).

## Manual steps for the owner or adopter

- None required.
- The `posthog-js` lint ban takes effect with the already pending wiring step (spec §23 step 4).
- Optional, with sandbox keys (DEC-006): set `SENTRY_TRACES_SAMPLE_RATE` above 0 and check in the Sentry dashboard that
  API transactions show up for requests made from the browser.
- DEC-001 proof checklist (required evidence item 7), on the authorized preview: a non-diagnostics 500 and a
  `checkout_started` sent to an otherwise idle instance both arrive in Sentry and PostHog. Label the result `sandbox`.

## Documentation changes needed (integrator)

- `docs/integrations.md`
  - Sentry section: browser requests carry no sampling decision, so the API samples them at `SENTRY_TRACES_SAMPLE_RATE`;
    the browser records no spans.
  - Line 132: only allowlisted events are sent, enforced in `before_send` at runtime in the browser (and `posthog-js` is
    importable only from `lib/analytics.ts`, by lint), and by the container's `AnalyticsEvents` in the API. Add the
    recipe below.
- `docs/data-inventory.md`
  - Logs: the pseudonymous internal user and organization UUIDs are written on every line of an authenticated request
    (drop "never include user IDs"). Sentry error events: tags `user_id` and `org_id` (internal UUIDs), not the user.
  - Analytics: the allowlist is the kernel's plus the modules'; in the browser, properties that neither start with `$` nor
    are allowlisted are dropped (the SDK's `$session_entry_utm_*` and `$initial_utm_*` keys stay).
- `docs/operations.md`: the "Reference" users see is the `X-Request-ID`; filter logs by `request_id`, `user_id`, `org_id`;
  telemetry flush (`telemetry_flush_incomplete`, `telemetry_flush_skipped`, `telemetry_flush_failed`).
- `docs/architecture.md`: the API relies on Fluid compute for the after-response wait (on Fluid, Vercel sends `end` only
  after the app returns, so the wait is equivalent to `wait_until`; the legacy non-Fluid mode returns as soon as the
  response ends). Add "Adding an analytics event" and "Adding error context" (below) next to "Adding a feature".
- `dev-docs/engineering-implementation-plan.md`: matrix rows OBS-03 (runtime enforcement, lint guard, registration),
  OBS-04 (`test_request_telemetry.py`), OBS-05 (reference, `request_id` tag), OBS-06 (`test_telemetry_flush.py`, wire
  test `arrived`), OBS-08 (`test_telemetry_wire.py`).

### Recipe: add an analytics event

API:

1. In the module's `module.py`, return it in the contribution:
   `Contribution(routers=[...], analytics_events={"report_exported": frozenset({"format"})})`.
2. Capture it from a use case through the `Analytics` port: in a request with
   `analytics.capture(user_id, "report_exported", {"format": "csv"})` (queued; flushed after the response), or from an
   outbox handler with `event_id=` and `occurred_at=` (sent and confirmed, retried by the outbox).
3. Add it to `docs/data-inventory.md`. Names and properties are lower-case snake case; never `email`, `name`-like personal
   data, tokens, or prompts.
4. A test: build the container with `RecordingAnalytics` (tests/conftest.py); it filters by the container's registry.

Web: add the event and its properties to `ANALYTICS_EVENTS` in `lib/analytics.ts` and call `track("name", {property})`
where it happens (early calls are kept until identity settles). Do not import `posthog-js` elsewhere.

### Recipe: add error context

API: do not put personal data in exception messages or log arguments (patterns are redacted, free text is not). The
request ID and the internal user and organization IDs are added for you. For another pseudonymous ID of the request
(for example a project ID), pass it as a log `extra={"project_id": ...}`; it is written on that line. To tag Sentry
events with more, add it in `_scrub_common` (one place), never `sentry_sdk.set_user`.

Web: an `ApiError` carries `requestId`. Show it to the user with `ErrorState`, and let the error reach Sentry as the
original exception (`captureException(error)` or an unhandled rejection): `prepareReport` tags `request_id`.

## Limitations

- An opaque string in the path of an unmatched URL (for example `/api/v1/orgs/<token>/nothing`) is sent as the
  transaction name and `request.url`: emails and token-shaped strings are redacted, other text cannot be recognised.
  A matched route is named by its template. Follow-up: name unmatched transactions `unmatched` and drop `request.url`
  for them (`scrub_transaction`; the existing `test_sentry_tracing.py` assertions that expect the redacted raw path would
  change). It also bounds the number of distinct transaction names an attacker can create (OBS-07).
- Exception messages and log messages are only pattern-redacted: free text such as a prompt in an exception message is
  not recognised. The wire test puts prompts where the scrubber can know them (keys such as `prompt` and `messages`, the
  request body).
- The web allowlist keeps any `$`-prefixed property name (they are the SDK's own), so a direct capture of an allowlisted
  event with a custom `$`-key passes; the lint ban is the control for that.
- Web event properties that neither start with `$` nor are allowlisted are dropped, including a plain `utm_source` key
  if an SDK version adds one (the installed version, checked on a landing URL with `utm_source`, sends
  `$session_entry_utm_source`, which stays). Add the key to the event's allowlist if it is wanted.
- `scrub_event` marks the request for every event it sees, including events the SDK later drops by sampling or
  de-duplication; the cost is an unneeded flush.
- The flush thread is outside the request-thread budget and holds at most one thread at a time.
- A server-side Sentry (`sentry.server.config.ts`, the Next.js server) is unchanged.

## Decisions taken (owner delegated; reversible)

- **Sentry tags `user_id` and `org_id`** on API error events, although the verifier called Sentry tags a privacy-model
  change the spec does not require (the task scope asked for them). Internal UUIDs only; `user` stays removed.
  Alternative: logs only (delete the three lines in `_scrub_common`, and the tag assertion in `test_request_telemetry.py`).
- **`Contribution.analytics_events`** although the verifier recommended dropping the registry (ARCH-11: one reviewable
  allowlist). Kept the review point: the kernel's list stays, every registration is validated, and docs list all events.
  Alternative: edit `ANALYTICS_ALLOWLIST` for each event.
- **Unknown-4xx copy** "Could not complete this" (the title `apix` proposed), action none.
- **Newest events dropped past 20** early events (the briefing's choice); alternative: drop the oldest.
- **Browser sets no sampling rate at all** (the briefing's verifier-corrected fix) instead of a rate that mirrors the
  API's, which the browser cannot know.

## Evidence (labelled `mocked` or `local`; none is `sandbox` or `live`)

API: from `apps/api` with `uv run --offline`, against the shared test PostgreSQL 17
(`TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54360/app`). Web: `pnpm --filter web ...`.

- `local`: `ruff check app tests index.py ../../perf`, `ruff format --check`, `lint-imports` (8 kept, 0 broken), `mypy`
  (84 files): clean.
- `local`: full API suite, `uv run pytest -q -p no:cacheprovider`: 1013 passed (the `apix` base had 974). Run once.
- `local`: web `lint`, `typecheck`, `format:check` clean; `pnpm --filter web test`: 418 passed, 1 skipped (the pending
  "are applied by eslint.config.mjs" test), 31 files. The first run had one failure, a fixed 50 ms wait in the new
  real-SDK direct-capture test on a loaded machine; fixed by polling (commit `test(web): the real-SDK analytics tests
  wait...`), then passed. `pnpm build` (placeholder Clerk key) succeeded; `pnpm contracts:check`: up to date.
- `mocked` (real SDKs, captured transports): browser SDK headers for a rate of 0 (`-0`, `sentry-sampled=false`) and no
  rate (no flag); `sentry-sdk` inherits `-0` and samples an undecided request at its rate; the PostHog events of the
  new tests; the wire harness (`test_telemetry_wire.py`) on loopback servers.
- Mutation runs (each reverted after the run; the named tests failed):
  - browser `tracesSampleRate: 0` back: the new `sentry-config.test.ts` test;
  - `presentError` default branch as before: the new `client.test.ts` test;
  - `before_send` without `allowlistEvent`: the direct-capture test; no buffering in `track`: 5 of the 6 pending tests;
    no discard on a user change: the user-switch test; cap 25: the cap test; no `posthog-js` pattern in the guard: 3
    `boundaries.test.ts` tests;
  - `ErrorState` reference for 4xx too: the state-views test; browser `beforeSend` without `prepareReport`: 3 tests;
  - tenant never bound / Clerk IDs bound instead: 4 `test_request_telemetry.py` tests each; no Sentry ID tags: 1;
  - middleware never flushes: the 500-flush test; PostHog queue does not mark the request: the analytics flush test;
    `scrub_event` does not mark: the 500-flush test; unbounded wait and no single-flight lock: the stuck-flush test;
  - contributions not registered / PostHog ignoring the registry / sensitive property names allowed: the matching
    `test_analytics_events.py` tests;
  - wire harness: no `before_send_transaction` (the email in an unmatched path reaches the transaction name), no
    `before_send`, PostHog unfiltered, log extras unredacted, no flush: each failed the wire test that covers it.
