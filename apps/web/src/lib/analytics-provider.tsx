"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useRef } from "react";

import { identify, resetAnalytics, startAnalytics } from "@/lib/analytics";
import { useApi } from "@/lib/api/use-api";

/** Starts analytics (if enabled) and identifies users by internal ID only. */
export function AnalyticsProvider() {
  const { isSignedIn } = useAuth();
  const api = useApi();
  const identified = useRef<string | null>(null);

  useEffect(() => {
    startAnalytics();
  }, []);

  useEffect(() => {
    if (!isSignedIn) {
      if (identified.current) resetAnalytics();
      identified.current = null;
      return;
    }
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled && identified.current !== me.user_id) {
          identify(me.user_id);
          identified.current = me.user_id;
        }
      })
      .catch(() => {
        /* analytics identification is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [api, isSignedIn]);

  return null;
}
