"use client";

import { useEffect, useState } from "react";

import type { Finding, MergePreview, MergeSuppression } from "@/lib/types";

interface Props {
  preview: MergePreview;
  onRecord: (decisions: MergeSuppression[]) => void;
  onClose: () => void;
}

function keyOf(finding: Finding): string {
  return `${finding.rule_id}:${finding.feature_id ?? ""}`;
}

/**
 * A merge refused for landing on a design rule neither side broke.
 *
 * There is no "merge anyway" button here, and that is the point. A frameshift
 * mutant is real work somebody may be doing deliberately, so that gate has a
 * flag. This one opens only against a reason, which goes into the merge commit
 * as a `suppress_finding` operation — so the next person to read the history
 * finds out who decided this was fine, and what the sequence looked like when
 * they did.
 */
export function MergeRuleBlock({ preview, onRecord, onClose }: Props) {
  const [reasons, setReasons] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      preview.new_findings.map((f) => [keyOf(f), f.suppression?.reason ?? ""]),
    ),
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const decisions: MergeSuppression[] = preview.new_findings
    .filter((f) => f.feature_id)
    .map((f) => ({
      rule_id: f.rule_id,
      feature_id: f.feature_id as string,
      reason: (reasons[keyOf(f)] ?? "").trim(),
    }));
  const ready =
    decisions.length === preview.new_findings.length &&
    decisions.every((d) => d.reason.length >= 3);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-baseline gap-3 border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">Merge blocked</h2>
          <p className="text-xs text-slate-500">
            The coordinates merge cleanly. The design rules do not.
          </p>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded px-2 text-slate-400 hover:bg-slate-100"
            aria-label="Close"
          >
            ×
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {preview.new_findings.map((finding) => {
            const key = keyOf(finding);
            const stale = finding.suppression?.stale ?? false;
            return (
              <div key={key} className="rounded border border-red-200 bg-red-50 p-3">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-medium text-red-900">
                    {finding.feature_name}
                  </span>
                  <span className="font-mono text-[11px] text-red-700">
                    {finding.rule_id}
                  </span>
                </div>
                <p className="mt-1 text-xs text-red-900">{finding.message}</p>

                {stale && finding.suppression && (
                  <div className="mt-2 text-[11px] leading-relaxed text-red-800">
                    <p>
                      Somebody had already decided this was deliberate —{" "}
                      <em>“{finding.suppression.reason}”</em> — but the bases
                      that decision was made about are not these bases any more.
                    </p>
                    {/* Stacked and aligned: the whole question the reader has
                        is which letters moved, and prose hides that. */}
                    <dl className="mt-1.5 overflow-x-auto">
                      {[
                        ["when suppressed", finding.suppression.was],
                        ["after the merge", finding.suppression.now],
                      ].map(([label, reading]) => (
                        <div key={label} className="flex gap-2 whitespace-nowrap">
                          <dt className="w-28 shrink-0 text-right text-red-700">
                            {label}
                          </dt>
                          <dd className="font-mono text-red-900">{reading}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}

                <p
                  className="mt-2 text-[11px] text-red-800"
                  title={`${finding.evidence.citation} — ${finding.evidence.organism} (${finding.evidence.confidence})`}
                >
                  {finding.evidence.citation}
                </p>

                <textarea
                  rows={2}
                  value={reasons[key] ?? ""}
                  onChange={(event) =>
                    setReasons((current) => ({
                      ...current,
                      [key]: event.target.value,
                    }))
                  }
                  placeholder="Still deliberate? Say why, against the merged sequence."
                  className="mt-2 w-full rounded border border-red-300 bg-white px-1.5 py-1 text-[11px] text-slate-700 focus:border-red-500 focus:outline-none"
                />
              </div>
            );
          })}

          <p className="max-w-2xl text-xs leading-relaxed text-slate-600">
            Neither branch breaks this rule on its own; the combination does.
            There is no override flag here, because the confidence behind an{" "}
            <code className="font-mono text-[11px]">error</code> rule is checked
            when the rule is loaded — nothing can declare itself blocking on a
            number nobody measured. What does get through is a decision:
            recorded in the merge commit as an operation, with your reason and
            the bases it was made about.
          </p>
        </div>

        <footer className="flex items-center gap-2 border-t border-slate-200 px-5 py-3">
          <p className="flex-1 text-[11px] text-slate-500">
            {ready
              ? "This will be recorded in the history alongside the merge."
              : "Every blocking finding needs a reason before the merge can go through."}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-700 hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!ready}
            onClick={() => onRecord(decisions)}
            className="rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-800 hover:bg-red-50 disabled:opacity-40"
          >
            Merge, recording {decisions.length === 1 ? "this" : "these"}
          </button>
        </footer>
      </div>
    </div>
  );
}
