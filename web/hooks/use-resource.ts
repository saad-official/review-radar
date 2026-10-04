"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { asApiError, type ApiError } from "@/lib/errors";

export type Resource<T> = {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  /** Re-fetch, keeping the current data on screen until the new data arrives. */
  reload: () => void;
  /** Replace the data locally (optimistic updates). */
  mutate: (update: (prev: T | undefined) => T | undefined) => void;
};

/**
 * Fetch on mount and whenever `key` changes. State is only set from promise
 * callbacks (never synchronously in the effect), and late answers from an older
 * key are ignored.
 */
export function useResource<T>(key: string | null, fetcher: () => Promise<T>): Resource<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [settledKey, setSettledKey] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (key === null) return;
    let alive = true;
    fetcherRef.current().then(
      (d) => {
        if (!alive) return;
        setData(d);
        setError(undefined);
        setSettledKey(key);
      },
      (e: unknown) => {
        if (!alive) return;
        setError(asApiError(e));
        setSettledKey(key);
      },
    );
    return () => {
      alive = false;
    };
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const mutate = useCallback((update: (prev: T | undefined) => T | undefined) => setData((prev) => update(prev)), []);

  return { data, error, loading: key !== null && settledKey !== key && data === undefined, reload, mutate };
}
