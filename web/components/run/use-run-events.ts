"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  eventStatus,
  getRun,
  isRetryableProcessError,
  isTerminal,
  mergeEvent,
  processingPromise,
  startProcessing,
  subscribeToRun,
  type ProcessOutcome,
  type RunEvent,
  type RunStatus,
} from "@/lib/api";
import { asApiError, type ApiError } from "@/lib/errors";

export type Connection = "idle" | "connecting" | "live" | "polling" | "closed";

const POLL_MS = 2500;

/**
 * Live events for one run: SSE (replay, then tail) until done|failed, falling
 * back to polling `GET /api/runs/{id}` when the stream breaks. Mount it keyed by
 * run id; pass `enabled: false` for runs already known to be finished.
 */
export function useRunEvents(runId: string, enabled: boolean) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [status, setStatus] = useState<RunStatus | undefined>(undefined);
  const [connection, setConnection] = useState<Connection>("idle");
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = null;
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let ended = false;
    let errors = 0;
    let unsubscribe: () => void = () => {};

    const startPolling = () => {
      if (pollTimer.current || ended) return;
      setConnection("polling");
      pollTimer.current = setInterval(async () => {
        try {
          const r = await getRun(runId);
          setStatus(r.status);
          if (isTerminal(r.status)) {
            ended = true;
            stopPolling();
            setConnection("closed");
          }
        } catch {
          // transient; the next tick retries
        }
      }, POLL_MS);
    };

    const t = setTimeout(() => setConnection((c) => (c === "idle" ? "connecting" : c)), 0);
    unsubscribe = subscribeToRun(runId, {
      onOpen: () => {
        errors = 0;
        setConnection("live");
      },
      onEvent: (e) => {
        setEvents((prev) => mergeEvent(prev, e));
        const s = eventStatus(e);
        if (s) setStatus(s);
        if (s && isTerminal(s)) {
          ended = true;
          unsubscribe();
          setConnection("closed");
        }
      },
      onError: (fatal) => {
        if (ended) return;
        errors += 1;
        if (fatal || errors >= 3) {
          unsubscribe();
          startPolling();
        }
      },
    });
    return () => {
      clearTimeout(t);
      unsubscribe();
      stopPolling();
    };
  }, [runId, enabled, stopPolling]);

  return { events, status, connection };
}

export type ProcessState = { state: "idle" | "running" | "ok" } | { state: "error"; error: ApiError; retryable: boolean };

/** Follow (or start) the resumable `/process` loop for a run started in this tab. */
export function useProcessing(runId: string) {
  const [state, setState] = useState<ProcessState>(() => (processingPromise(runId) ? { state: "running" } : { state: "idle" }));
  const alive = useRef(true);

  const attach = useCallback((p: Promise<ProcessOutcome>) => {
    p.then(
      () => {
        if (alive.current) setState({ state: "ok" });
      },
      (e: unknown) => {
        if (!alive.current) return;
        const error = asApiError(e);
        setState({ state: "error", error, retryable: isRetryableProcessError(error) });
      },
    );
  }, []);

  useEffect(() => {
    alive.current = true;
    const p = processingPromise(runId);
    if (p) attach(p);
    return () => {
      alive.current = false;
    };
  }, [runId, attach]);

  const start = useCallback(
    (force = false) => {
      setState({ state: "running" });
      attach(startProcessing(runId, { force }));
    },
    [runId, attach],
  );

  return { processState: state, startProcessing: start };
}
