"use client";

import posthog, { type CaptureResult, type Properties } from "posthog-js";

import { publicEnv } from "./env";
import { SENSITIVE_QUERY_PARAMETERS, stripQuery } from "./redact";

/**
 * Product analytics with an explicit allowlist (OBS-03). Events and
 * properties outside this map are dropped: `before_send` enforces it for
 * everything the SDK sends, so a direct posthog.capture() is held to it too
 * (and lint bans importing posthog-js outside this file). Users are
 * identified by the internal user ID only — never by email or name.
 *
 * To add an event, add it here, and to docs/data-inventory.md; call
 * `track("name", { property })` where it happens.
 */
export const ANALYTICS_EVENTS = {
  upgrade_clicked: ["plan"],
  paywall_viewed: ["feature"],
  billing_portal_clicked: [],
  integration_test_sent: ["source"],
} as const satisfies Record<string, readonly string[]>;

export type AnalyticsEvent = keyof typeof ANALYTICS_EVENTS;
type Props<E extends AnalyticsEvent> = Partial<Record<(typeof ANALYTICS_EVENTS)[E][number], string>>;

let started = false;

export function analyticsEnabled(): boolean {
  return publicEnv.analytics.enabled;
}

/**
 * Every capture feature outside the allowlist is pinned off here, because the
 * PostHog project's remote settings would otherwise switch it on for this
 * site without a code change: exception, heatmap, dead-click, performance,
 * web-vitals, and console-log capture, session recording, surveys, and tours.
 * The /flags request (feature flags and remote configuration) is off too: the
 * template uses no flags, and that request sends the first-seen URL and
 * referrer as person properties, which `before_send` cannot scrub.
 */
const PINNED_OFF = {
  advanced_disable_flags: true,
  autocapture: false,
  rageclick: false,
  capture_exceptions: false,
  capture_heatmaps: false,
  capture_dead_clicks: false,
  capture_performance: false,
  capture_webmcp: false,
  logs: { captureConsoleLogs: false },
  disable_session_recording: true,
  disable_surveys: true,
  disable_product_tours: true,
  disable_conversations: true,
  disable_web_experiments: true,
} as const;

/**
 * The events the SDK sends on its own: a pageview on every navigation, and
 * the identification of the signed-in user. Switching a feature in PINNED_OFF
 * back on means adding its events here, or `before_send` drops them.
 */
const SDK_EVENTS: ReadonlySet<string> = new Set(["$pageview", "$identify"]);
/** The properties every event needs to be ingested. */
const REQUIRED_PROPERTIES = ["distinct_id", "token"];
/** Person properties are set through `$set`, `$set_once`, and `$unset`; only `$set_once` may carry the SDK's own. */
const PERSON_PROPERTIES = ["$set", "$set_once", "$unset"];

/** Properties never sent, wherever the SDK puts them. */
const DENYLIST = ["$current_url", "$referrer", "$initial_referrer"];
/** Keys whose values are URLs or paths: the SDK's own (for example $session_entry_url, $initial_current_url) and any added later. */
const URL_KEY = /(?:url|referrer|pathname|href)$/i;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null) return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function scrubValue(key: string, value: unknown): unknown {
  if (typeof value === "string") return URL_KEY.test(key) ? stripQuery(value) : value;
  if (Array.isArray(value)) return value.map((item) => scrubValue(key, item));
  return isPlainObject(value) ? scrubProperties(value) : value;
}

function scrubProperties(properties: Record<string, unknown>): Properties {
  return Object.fromEntries(
    Object.entries(properties)
      .filter(([key]) => !DENYLIST.includes(key))
      .map(([key, value]) => [key, scrubValue(key, value)]),
  ) as Properties;
}

function isAnalyticsEvent(name: string): name is AnalyticsEvent {
  return Object.keys(ANALYTICS_EVENTS).includes(name);
}

/** The entries of person properties the SDK itself adds (`$initial_*`): its names start with "$". */
function sdkPersonProperties(properties: unknown): Properties | undefined {
  if (!isPlainObject(properties)) return undefined;
  const own = Object.entries(properties).filter(([key]) => key.startsWith("$"));
  return own.length ? (Object.fromEntries(own) as Properties) : undefined;
}

/**
 * OBS-03 at runtime, for everything the SDK sends. An event outside
 * ANALYTICS_EVENTS and SDK_EVENTS goes nowhere. Of the rest, only these stay:
 * the properties the event's allowlist names (as strings), the SDK's own
 * "$"-prefixed ones, and what ingestion needs. `$set` and `$unset` are
 * dropped, and `$set_once` keeps the SDK's own entries, so no personal data
 * set with register(), identify(), setPersonProperties(), or a capture option
 * leaves the browser. Returns null to drop the event.
 */
function allowlistEvent(event: CaptureResult): CaptureResult | null {
  const name: string = event.event;
  const allowed: readonly string[] | undefined = isAnalyticsEvent(name)
    ? ANALYTICS_EVENTS[name]
    : SDK_EVENTS.has(name)
      ? []
      : undefined;
  if (!allowed) return null;
  const properties: Properties = {};
  for (const [key, value] of Object.entries(event.properties)) {
    if (key === "$set_once") {
      const own = sdkPersonProperties(value);
      if (own) properties[key] = own;
    } else if (allowed.includes(key)) {
      if (typeof value === "string") properties[key] = value;
    } else if (
      REQUIRED_PROPERTIES.includes(key) ||
      (key.startsWith("$") && !PERSON_PROPERTIES.includes(key))
    ) {
      properties[key] = value;
    }
  }
  const result: CaptureResult = { ...event, properties };
  delete result.$set;
  delete result.$unset;
  const own = sdkPersonProperties(event.$set_once);
  if (own) result.$set_once = own;
  else delete result.$set_once;
  return result;
}

/**
 * Runs on every event just before it is sent (`before_send`). The SDK adds
 * URLs on its own — the session's entry URL and referrer on every event, the
 * first-seen URL and referrer as person properties on $identify — and query
 * strings and fragments can carry tokens (Clerk tickets, Stripe session IDs).
 * Each URL-like value in properties, $set, and $set_once keeps only its
 * origin and path, and denylisted keys are dropped there too.
 */
export function scrubEvent(event: CaptureResult): CaptureResult {
  return {
    ...event,
    properties: scrubProperties(event.properties),
    ...(event.$set && { $set: scrubProperties(event.$set) }),
    ...(event.$set_once && { $set_once: scrubProperties(event.$set_once) }),
  };
}

/** A copy of a stored `{ r, u }` record with only the origin and path of its referrer `r`, if that differs. */
function withPlainReferrer(info: unknown): Record<string, unknown> | undefined {
  if (!isPlainObject(info) || typeof info.r !== "string" || stripQuery(info.r) === info.r) return undefined;
  return { ...info, r: stripQuery(info.r) };
}

/**
 * The SDK also stores referrers, where before_send never reaches and which
 * mask_personal_data_properties leaves as they are: the first-seen and each
 * session's entry referrer (localStorage) and the session's $referrer
 * (sessionStorage). A page reached from another page of this site has that
 * page's full URL as its referrer, query and fragment included, so each keeps
 * only its origin and path, as in what is sent. The SDK writes them at the
 * start of a capture, before before_send runs, and when a session starts.
 */
function scrubStoredReferrers(): void {
  try {
    const { persistence, sessionPersistence } = posthog;
    const firstSeen = withPlainReferrer(persistence?.props.$initial_person_info);
    if (firstSeen) persistence?.register({ $initial_person_info: firstSeen });
    const sessionProps: unknown = persistence?.props.$client_session_props;
    if (isPlainObject(sessionProps)) {
      const sessionEntry = withPlainReferrer(sessionProps.props);
      if (sessionEntry) {
        persistence?.register({ $client_session_props: { ...sessionProps, props: sessionEntry } });
      }
    }
    const referrer: unknown = sessionPersistence?.props.$referrer;
    if (typeof referrer === "string" && stripQuery(referrer) !== referrer) {
      sessionPersistence?.register({ $referrer: stripQuery(referrer) });
    }
  } catch {
    /* ignored */
  }
}

export function startAnalytics(): void {
  if (started || !analyticsEnabled() || typeof window === "undefined") return;
  try {
    posthog.init(publicEnv.analytics.key, {
      api_host: publicEnv.analytics.host,
      ...PINNED_OFF,
      capture_pageview: "history_change",
      capture_pageleave: false,
      person_profiles: "identified_only",
      mask_all_text: true,
      mask_all_element_attributes: true,
      persistence: "localStorage",
      // The SDK keeps the first-seen URL and each session's entry URL in storage, where
      // before_send never reaches: token parameters (and ad click IDs) are masked there.
      // The referrers stored with them are not masked; scrubStoredReferrers strips them.
      mask_personal_data_properties: true,
      custom_personal_data_properties: [...SENSITIVE_QUERY_PARAMETERS],
      disable_capture_url_hashes: true,
      property_denylist: DENYLIST,
      before_send: (event) => {
        scrubStoredReferrers();
        const allowed = allowlistEvent(event);
        return allowed && scrubEvent(allowed);
      },
    });
    started = true;
    posthog.onSessionId(scrubStoredReferrers);
  } catch {
    // Analytics must never break the application (OBS-06).
  }
}

/**
 * Identifies the signed-in user by internal ID. An identity left from someone
 * else (for example, kept in storage from an earlier visit) is cleared first,
 * so two people are never merged into one PostHog person.
 */
export function identify(internalUserId: string): void {
  if (!started) return;
  try {
    if (posthog._isIdentified() && posthog.get_distinct_id() !== internalUserId) posthog.reset(true);
    posthog.identify(internalUserId);
  } catch {
    /* ignored */
  }
}

/**
 * Starts a new anonymous identity, including a new device ID, so later events
 * cannot be tied to the previous user. With `onlyIfIdentified`, an anonymous
 * visitor keeps their current anonymous identity.
 */
export function resetAnalytics({ onlyIfIdentified = false } = {}): void {
  if (!started) return;
  try {
    if (onlyIfIdentified && !posthog._isIdentified()) return;
    posthog.reset(true);
  } catch {
    /* ignored */
  }
}

export function filterProperties<E extends AnalyticsEvent>(
  event: E,
  props: Props<E>,
): Record<string, string> {
  const allowed: readonly string[] = ANALYTICS_EVENTS[event];
  return Object.fromEntries(
    Object.entries(props).filter(([k, v]) => allowed.includes(k) && typeof v === "string"),
  ) as Record<string, string>;
}

/**
 * Events tracked before analytics has started, which waits for the signed-in
 * user's internal ID: kept, with the time they happened, and sent by
 * flushPendingEvents() once identity is settled. At most MAX_PENDING_EVENTS are
 * kept (OBS-06); later ones are dropped.
 */
const MAX_PENDING_EVENTS = 20;
type PendingEvent = { event: AnalyticsEvent; props: Record<string, string>; at: Date };
let pending: PendingEvent[] = [];

function capture(event: AnalyticsEvent, props: Record<string, string>, at?: Date): void {
  try {
    if (at) posthog.capture(event, props, { timestamp: at });
    else posthog.capture(event, props);
  } catch {
    /* ignored */
  }
}

export function track<E extends AnalyticsEvent>(event: E, props: Props<E> = {}): void {
  if (!analyticsEnabled() || typeof window === "undefined") return;
  const filtered = filterProperties(event, props);
  if (started) capture(event, filtered);
  else if (pending.length < MAX_PENDING_EVENTS) pending.push({ event, props: filtered, at: new Date() });
}

/**
 * Sends the events tracked before analytics started, as whoever it identified
 * (or as the anonymous visitor). Call it once identity is settled.
 */
export function flushPendingEvents(): void {
  if (!started) return;
  const events = pending;
  pending = [];
  for (const { event, props, at } of events) capture(event, props, at);
}

/** Forgets the events tracked before analytics started: for a user who changed meanwhile, or who declined (consent, DEC-004). */
export function discardPendingEvents(): void {
  pending = [];
}

/** Sends one allowlisted test event immediately (integrations page). */
export function sendTestEvent(): "sent" | "disabled" {
  if (!started) return "disabled";
  try {
    posthog.capture("integration_test_sent", filterProperties("integration_test_sent", { source: "web" }), {
      send_instantly: true,
    });
    return "sent";
  } catch {
    return "disabled";
  }
}
