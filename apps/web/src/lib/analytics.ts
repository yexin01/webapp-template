"use client";

import posthog from "posthog-js";

/**
 * Product analytics with an explicit allowlist (OBS-03). Events and
 * properties outside this map are dropped. Users are identified by the
 * internal user ID only — never by email or name.
 */
export const ANALYTICS_EVENTS = {
  upgrade_clicked: ["plan"],
  paywall_viewed: ["feature"],
  billing_portal_clicked: [],
} as const satisfies Record<string, readonly string[]>;

export type AnalyticsEvent = keyof typeof ANALYTICS_EVENTS;
type Props<E extends AnalyticsEvent> = Partial<Record<(typeof ANALYTICS_EVENTS)[E][number], string>>;

let started = false;

export function analyticsEnabled(): boolean {
  return process.env.NEXT_PUBLIC_ANALYTICS_ENABLED === "true" && Boolean(process.env.NEXT_PUBLIC_POSTHOG_KEY);
}

export function startAnalytics(): void {
  if (started || !analyticsEnabled() || typeof window === "undefined") return;
  try {
    posthog.init(process.env.NEXT_PUBLIC_POSTHOG_KEY ?? "", {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com",
      autocapture: false,
      capture_pageview: "history_change",
      capture_pageleave: false,
      disable_session_recording: true,
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
