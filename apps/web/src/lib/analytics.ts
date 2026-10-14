"use client";

import posthog from "posthog-js";

import { publicEnv } from "./env";

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
 */
const PINNED_OFF = {
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
      property_denylist: ["$current_url", "$referrer", "$initial_referrer"],
      sanitize_properties: (properties) => {
        // Strip query strings and fragments, which may carry tokens or IDs.
        for (const key of ["$current_url", "$pathname", "$referrer"]) {
          const value = properties[key];
          if (typeof value === "string") properties[key] = value.split(/[?#]/)[0];
        }
        return properties;
      },
    });
    started = true;
  } catch {
    // Analytics must never break the application (OBS-06).
  }
}

export function identify(internalUserId: string): void {
  if (!started) return;
  try {
    posthog.identify(internalUserId);
  } catch {
    /* ignored */
  }
}

export function resetAnalytics(): void {
  if (!started) return;
  try {
    posthog.reset();
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
