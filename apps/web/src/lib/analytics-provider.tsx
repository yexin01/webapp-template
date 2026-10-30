"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useRef } from "react";

import {
  analyticsEnabled,
  discardPendingEvents,
  flushPendingEvents,
  identify,
  resetAnalytics,
  startAnalytics,
} from "@/lib/analytics";
import { useApi } from "@/lib/api/use-api";

/**
 * Starts analytics (if enabled) and identifies users by internal ID only.
 *
 * PostHog sends its first pageview on the tick after it starts, with whatever
 * identity it restored from storage. So analytics starts only once Clerk has
 * loaded and, for a signed-in user, the internal ID is known, and the
 * identity is settled in the same task: a stored identity of someone else
 * (an earlier visitor of this browser) is reset before any event is sent.
 * Afterwards identity follows the Clerk user: switching accounts in a
 * multi-session browser or signing out resets it before the next user.
 *
 * Events tracked meanwhile are kept (see track) and sent once identity is
 * settled; if the user changes first, they are dropped, not sent as the next one.
 */
export function AnalyticsProvider() {
  const { isLoaded, userId } = useAuth();
  const api = useApi();
  // The Clerk user whose internal ID analytics is currently identified with.
  const identifiedFor = useRef<string | null>(null);
  // The Clerk user the events tracked before analytics started belong to.
  const pendingFor = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (!analyticsEnabled() || !isLoaded) return;
    if (pendingFor.current !== undefined && pendingFor.current !== (userId ?? null)) discardPendingEvents();
    pendingFor.current = userId ?? null;
    if (identifiedFor.current && identifiedFor.current !== userId) {
      resetAnalytics();
      identifiedFor.current = null;
    }
    if (!userId) {
      startAnalytics();
      resetAnalytics({ onlyIfIdentified: true });
      flushPendingEvents();
      return;
    }
    if (identifiedFor.current === userId) return;
    let cancelled = false;
    api.me().then(
      (me) => {
        if (cancelled) return;
        startAnalytics();
        identify(me.user_id);
        identifiedFor.current = userId;
        flushPendingEvents();
      },
      () => {
        if (cancelled) return;
        // Without the internal ID, a stored identity cannot be confirmed as this user's.
        startAnalytics();
        resetAnalytics({ onlyIfIdentified: true });
        flushPendingEvents();
      },
    );
    return () => {
      cancelled = true;
    };
  }, [api, isLoaded, userId]);

  return null;
}
