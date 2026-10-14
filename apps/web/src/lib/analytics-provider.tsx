"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useRef } from "react";

import { identify, resetAnalytics, startAnalytics } from "@/lib/analytics";
import { useApi } from "@/lib/api/use-api";

/**
 * Starts analytics (if enabled) and identifies users by internal ID only.
 * Identity follows the Clerk user: switching accounts in a multi-session
 * browser or signing out resets it before the next user is identified.
 */
export function AnalyticsProvider() {
  const { userId } = useAuth();
  const api = useApi();
  // The Clerk user whose internal ID analytics is currently identified with.
  const identifiedFor = useRef<string | null>(null);

  useEffect(() => {
    startAnalytics();
  }, []);

  useEffect(() => {
    if (identifiedFor.current && identifiedFor.current !== userId) {
      resetAnalytics();
      identifiedFor.current = null;
    }
    if (!userId || identifiedFor.current === userId) return;
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (cancelled) return;
        identify(me.user_id);
        identifiedFor.current = userId;
      })
      .catch(() => {
        /* analytics identification is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [api, userId]);

  return null;
}
