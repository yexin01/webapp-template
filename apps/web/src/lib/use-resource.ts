"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

type Settled<T> = { key: string; data?: T; error?: unknown };

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
 */
export function useResource<T>(fetcher: () => Promise<T>, key: string): Resource<T> {
  const { isLoaded, userId } = useAuth();
  const [nonce, setNonce] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });
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
  const fresh = settled.key === current;
  return {
    data: fresh ? settled.data : undefined,
    error: fresh ? settled.error : undefined,
    loading: !fresh,
    reload,
  };
}
