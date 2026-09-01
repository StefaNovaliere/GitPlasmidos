"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "./api";
import { predictSequence } from "./sequence";
import type { ConstructDetail, History, OperationKind } from "./types";

interface UseConstruct {
  construct: ConstructDetail | null;
  history: History | null;
  loading: boolean;
  /** True while an edit is in flight and the view may be showing a preview. */
  pending: boolean;
  error: string | null;
  apply: (
    kind: OperationKind,
    payload: Record<string, unknown>,
  ) => Promise<boolean>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  reload: () => Promise<void>;
}

/**
 * Owns the construct's derived state.
 *
 * Edits are optimistic: the predicted sequence is shown immediately, the
 * server's authoritative state replaces it on success, and the snapshot taken
 * before the call is restored on failure.
 */
export function useConstruct(
  id: string,
  onWarnings: (messages: string[]) => void,
  onError: (message: string) => void,
): UseConstruct {
  const [construct, setConstruct] = useState<ConstructDetail | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Warnings are recomputed on every replay, so only announce the new ones.
  const announced = useRef<Set<string>>(new Set());

  const absorb = useCallback(
    (next: ConstructDetail) => {
      setConstruct(next);
      const fresh = next.warnings.filter((w) => !announced.current.has(w));
      fresh.forEach((w) => announced.current.add(w));
      if (fresh.length) onWarnings(fresh);
    },
    [onWarnings],
  );

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.history(id));
    } catch {
      /* the history panel is not worth failing the page over */
    }
  }, [id]);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      absorb(await api.getConstruct(id));
      setError(null);
      await refreshHistory();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [id, absorb, refreshHistory]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const run = useCallback(
    async (
      action: () => Promise<ConstructDetail>,
      optimistic?: (current: ConstructDetail) => ConstructDetail,
    ): Promise<boolean> => {
      const snapshot = construct;
      if (!snapshot) return false;
      if (optimistic) setConstruct(optimistic(snapshot));
      setPending(true);
      try {
        absorb(await action());
        await refreshHistory();
        return true;
      } catch (err) {
        setConstruct(snapshot); // rollback
        onError(err instanceof ApiError ? err.message : String(err));
        return false;
      } finally {
        setPending(false);
      }
    },
    [construct, absorb, refreshHistory, onError],
  );

  const apply = useCallback(
    (kind: OperationKind, payload: Record<string, unknown>) =>
      run(
        () => api.applyOperation(id, kind, payload),
        (current) => {
          const predicted = predictSequence(
            current.sequence,
            current.is_circular,
            kind,
            payload,
          );
          if (predicted === null) return current;
          return {
            ...current,
            sequence: predicted,
            length: predicted.length,
            can_undo: true,
            can_redo: false,
          };
        },
      ),
    [id, run],
  );

  const undo = useCallback(async () => {
    if (!construct?.can_undo) return;
    await run(() => api.undo(id));
  }, [construct?.can_undo, id, run]);

  const redo = useCallback(async () => {
    if (!construct?.can_redo) return;
    await run(() => api.redo(id));
  }, [construct?.can_redo, id, run]);

  return { construct, history, loading, pending, error, apply, undo, redo, reload };
}
