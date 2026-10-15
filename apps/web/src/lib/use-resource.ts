"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

import { REQUEST_TIMEOUT_MS } from "./api/deadlines";
import { ApiError } from "./api/errors";

type Settled<T> = { key: string; data?: T; error?: unknown };

const AUTH_UNAVAILABLE = ApiError.authUnavailable();
/** A Clerk that failed to load starts again only with a fresh page. */
export const reloadPage = () => window.location.reload();

/**
 * True once Clerk has not loaded within the request deadline. Then it never
 * will: its script was blocked, or its CDN or Frontend API is down.
 */
export function useClerkLoadTimedOut(): boolean {
  const { isLoaded } = useAuth();
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (isLoaded) return;
    const timer = window.setTimeout(() => setTimedOut(true), REQUEST_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [isLoaded]);
  return timedOut && !isLoaded;
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
 * If Clerk has not loaded within the request deadline, it never will (its
 * script was blocked, or its CDN or Frontend API is down): the resource then
 * reports a "sign-in unavailable" error, and reload() reloads the page.
 */
export function useResource<T>(fetcher: () => Promise<T>, key: string): Resource<T> {
  const { isLoaded, userId } = useAuth();
  const [nonce, setNonce] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });
  const clerkTimedOut = useClerkLoadTimedOut();
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
  if (clerkTimedOut) return { data: undefined, error: AUTH_UNAVAILABLE, loading: false, reload: reloadPage };
  const fresh = settled.key === current;
  return {
    data: fresh ? settled.data : undefined,
    error: fresh ? settled.error : undefined,
    loading: !fresh,
    reload,
  };
}
