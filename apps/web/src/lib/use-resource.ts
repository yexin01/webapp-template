"use client";

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
 * stale data from a previous tenant or user is never shown.
 */
export function useResource<T>(fetcher: () => Promise<T>, key: string): Resource<T> {
  const [nonce, setNonce] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });
  const current = `${key}#${nonce}`;

  useEffect(() => {
    let cancelled = false;
    fetcher().then(
      (data) => !cancelled && setSettled({ key: current, data }),
      (error: unknown) => !cancelled && setSettled({ key: current, error }),
    );
    return () => {
      cancelled = true;
    };
  }, [fetcher, current]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const fresh = settled.key === current;
  return {
    data: fresh ? settled.data : undefined,
    error: fresh ? settled.error : undefined,
    loading: !fresh,
    reload,
  };
}
