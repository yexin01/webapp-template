"use client";

import posthog, { type CaptureResult, type Properties } from "posthog-js";

import { publicEnv } from "./env";
import { stripQuery } from "./redact";

/**
 * Product analytics with an explicit allowlist (OBS-03). Events and
 * properties outside this map are dropped. Users are identified by the
 * internal user ID only — never by email or name.
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
      property_denylist: DENYLIST,
      before_send: scrubEvent,
    });
    started = true;
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

export function track<E extends AnalyticsEvent>(event: E, props: Props<E> = {}): void {
  if (!started) return;
  try {
    posthog.capture(event, filterProperties(event, props));
  } catch {
    /* ignored */
  }
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
