"use client";

import { useAuth, useClerk } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./api/errors";

type Settled<T> = { key: string; data?: T; error?: unknown };

const AUTH_UNAVAILABLE = ApiError.authUnavailable();
/** A Clerk that failed to load starts again only with a fresh page. */
export const reloadPage = () => window.location.reload();

/**
 * How long to wait for a Clerk that has neither loaded nor reported a failure.
 * The SDK sets its status to "error" itself when clerk-js cannot be loaded
 * (after its own 15 s script timeout, 30 s for a script that never answers) or
 * Clerk.load() rejects. Only a load() that never settles reaches this timer.
 */
export const CLERK_LOAD_BACKSTOP_MS = 30_000;

/**
 * True once Clerk has not loaded and never will: the SDK reports status
 * "error" (its script was blocked, or its CDN or Frontend API refused), or it
 * has neither loaded nor failed within the backstop. A "degraded" Clerk has
 * loaded (its Frontend API is down, but the page works), so it is not
 * unavailable here.
 */
export function useClerkUnavailable(): boolean {
  const { isLoaded } = useAuth();
  const { status } = useClerk();
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (isLoaded) return;
    const timer = window.setTimeout(() => setTimedOut(true), CLERK_LOAD_BACKSTOP_MS);
    return () => window.clearTimeout(timer);
  }, [isLoaded]);
  return !isLoaded && (status === "error" || timedOut);
}

export type Resource<T> = {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
};

/**
 * Loads data for a key. State changes happen only when the request settles,
 * and a changed key is reported as loading until its own result arrives, so
 * stale data from a previous tenant or user is never shown. The signed-in
 * user is always part of the key: after a Clerk account switch the previous
 * user's data is hidden and refetched even if the caller's key is unchanged.
 * Nothing is fetched until Clerk has loaded (it reports loading meanwhile),
 * so the user's arrival on a fresh page load does not fetch everything twice.
 * If Clerk reports that it failed to load, or has not loaded within the
 * backstop (see useClerkUnavailable), it never will: the resource then
 * reports a "sign-in unavailable" error, and reload() reloads the page.
 */
export function useResource<T>(fetcher: () => Promise<T>, key: string): Resource<T> {
  const { isLoaded, userId } = useAuth();
  const [nonce, setNonce] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });
  const clerkUnavailable = useClerkUnavailable();
  const current = `${userId ?? "signed-out"}:${key}#${nonce}`;

  useEffect(() => {
    if (!isLoaded) return;
    let cancelled = false;
    fetcher().then(
      (data) => !cancelled && setSettled({ key: current, data }),
      (error: unknown) => !cancelled && setSettled({ key: current, error }),
    );
    return () => {
      cancelled = true;
    };
  }, [fetcher, current, isLoaded]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  if (clerkUnavailable)
    return { data: undefined, error: AUTH_UNAVAILABLE, loading: false, reload: reloadPage };
  const fresh = settled.key === current;
  return {
    data: fresh ? settled.data : undefined,
    error: fresh ? settled.error : undefined,
    loading: !fresh,
    reload,
  };
}
