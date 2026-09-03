"use client";

import type { History, OperationRecord } from "@/lib/types";

interface Props {
  history: History | null;
  canUndo: boolean;
  canRedo: boolean;
  busy: boolean;
  onUndo: () => void;
  onRedo: () => void;
}

/** A one-line, human-readable summary of an operation's payload. */
function describe(op: OperationRecord): string {
  const p = op.payload as Record<string, number | string | undefined>;
  switch (op.kind) {
    case "insert":
      return `${String(p.seq ?? "").length} bp at ${Number(p.pos) + 1}`;
    case "delete":
      return `${Number(p.start) + 1}..${p.end}`;
    case "replace":
      return `${Number(p.start) + 1}..${p.end} → ${String(p.seq ?? "").length} bp`;
    case "revcomp_region":
      return `${Number(p.start) + 1}..${p.end}`;
    case "set_origin":
      return `origin → ${Number(p.pos) + 1}`;
    case "add_feature": {
      const feature = op.payload.feature as { name?: string } | undefined;
      return feature?.name ?? "";
    }
    case "remove_feature":
    case "update_feature":
      return String(op.payload.feature_id ?? "");
    case "suppress_finding":
      return `${String(p.rule_id ?? "")} on ${String(p.feature_id ?? "")} — ${String(
        p.reason ?? "",
      )}`;
    case "unsuppress_finding":
      return `${String(p.rule_id ?? "")} on ${String(p.feature_id ?? "")}`;
    default:
      return "";
  }
}

export function HistoryPanel({
  history,
  canUndo,
  canRedo,
  busy,
  onUndo,
  onRedo,
}: Props) {
  const operations = history ? [...history.operations].reverse() : [];

  return (
    <section className="flex max-h-[45%] min-h-0 flex-col border-t border-slate-200">
      <header className="flex items-center justify-between px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          History
        </h2>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={onUndo}
            disabled={!canUndo || busy}
            title="Undo (Ctrl/Cmd+Z)"
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            ↶ Undo
          </button>
          <button
            type="button"
            onClick={onRedo}
            disabled={!canRedo || busy}
            title="Redo (Ctrl/Cmd+Shift+Z)"
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            ↷ Redo
          </button>
        </div>
      </header>

      <ol className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {operations.length === 0 && (
          <li className="px-2 py-4 text-center text-xs text-slate-400">
            No edits yet — the construct is exactly as imported.
          </li>
        )}
        {operations.map((op) => (
          <li
            key={op.id}
            className={`flex items-baseline gap-2 rounded px-2 py-1 text-xs ${
              op.reverted ? "text-slate-400 line-through" : "text-slate-700"
            }`}
            title={op.reverted ? "Undone — redo to reapply" : undefined}
          >
            <span className="w-5 shrink-0 text-right font-mono text-[10px] text-slate-400">
              {op.index}
            </span>
            <span className="font-medium">{op.kind}</span>
            <span className="truncate font-mono text-[11px] text-slate-500">
              {describe(op)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
