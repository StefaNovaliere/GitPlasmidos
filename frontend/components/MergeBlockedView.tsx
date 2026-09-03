"use client";

import { useEffect, useState } from "react";

import { FrameBreakBlock } from "@/components/FrameBreakBlock";
import type {
  ConstructDetail,
  Finding,
  MergePreview,
  MergeSuppression,
} from "@/lib/types";

interface Props {
  preview: MergePreview;
  target: ConstructDetail;
  onSubmit: (allowFrameBreaks: boolean, decisions: MergeSuppression[]) => void;
  onClose: () => void;
}

function keyOf(finding: Finding): string {
  return `${finding.rule_id}:${finding.feature_id ?? ""}`;
}

/**
 * One dialog for every reason a merge was refused.
 *
 * The two gates are genuinely different — a frameshift mutant is real work
 * somebody may be doing on purpose, so that one takes an acknowledgement; a
 * design-rule error opens only against a written reason, which goes into the
 * merge commit as an operation. But a merge has to clear both, and clearing
 * one only to be refused by the other is a worse experience than being told
 * everything at once. So both are settled here and sent in a single request.
 */
export function MergeBlockedView({ preview, target, onSubmit, onClose }: Props) {
  const frameIssue = preview.new_frame_issues.find((i) => i.blocking);
  const findings = preview.new_findings;

  const [acknowledged, setAcknowledged] = useState(false);
  const [reasons, setReasons] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      findings.map((f) => [keyOf(f), f.suppression?.reason ?? ""]),
    ),
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!frameIssue && findings.length === 0) return null;

  const decisions: MergeSuppression[] = findings
    .filter((f) => f.feature_id)
    .map((f) => ({
      rule_id: f.rule_id,
      feature_id: f.feature_id as string,
      reason: (reasons[keyOf(f)] ?? "").trim(),
    }));
  const ready =
    (!frameIssue || acknowledged) &&
    decisions.length === findings.length &&
    decisions.every((d) => d.reason.length >= 3);

  const subtitle = frameIssue
    ? findings.length > 0
      ? "The coordinates merge cleanly. The protein and the design rules do not."
      : "The coordinates merge cleanly. The protein does not."
    : "The coordinates merge cleanly. The design rules do not.";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-baseline gap-3 border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">Merge blocked</h2>
          <p className="text-xs text-slate-500">{subtitle}</p>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded px-2 text-slate-400 hover:bg-slate-100"
            aria-label="Close"
          >
            ×
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {frameIssue && (
            <>
              <FrameBreakBlock
                preview={preview}
                target={target}
                issue={frameIssue}
              />
              <label className="flex items-start gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  The truncation is what I meant to build. Blocking by default
                  is the point; blocking absolutely would be paternalistic.
                </span>
              </label>
            </>
          )}

          {findings.length > 0 && (
            <section className="space-y-3">
              {frameIssue && (
                <h3 className="border-t border-slate-200 pt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  And a design rule the merge lands on
                </h3>
              )}
              {findings.map((finding) => (
                <RuleCard
                  key={keyOf(finding)}
                  finding={finding}
                  reason={reasons[keyOf(finding)] ?? ""}
                  onReason={(text) =>
                    setReasons((current) => ({
                      ...current,
                      [keyOf(finding)]: text,
                    }))
                  }
                />
              ))}
              <p className="max-w-2xl text-xs leading-relaxed text-slate-600">
                There is no override flag for this one, because the confidence
                behind an <code className="font-mono text-[11px]">error</code>{" "}
                rule is checked when the rule is loaded — nothing can declare
                itself blocking on a number nobody measured. What gets through
                is a decision: recorded in the merge commit as an operation,
                with your reason and the bases it was made about.
              </p>
            </section>
          )}

          {preview.rule_pack && (
            <p className="text-[11px] text-slate-400">
              Judged against rule pack{" "}
              <span className="font-mono">
                {preview.rule_pack.digest.slice(0, 8)}
              </span>{" "}
              — {preview.rule_pack.rules} rule
              {preview.rule_pack.rules === 1 ? "" : "s"}
              {preview.rule_pack.errors.length > 0 &&
                `, ${preview.rule_pack.errors.length} rejected`}
              .
            </p>
          )}
        </div>

        <footer className="flex items-center gap-2 border-t border-slate-200 px-5 py-3">
          <p className="flex-1 text-[11px] text-slate-500">
            {ready
              ? findings.length > 0
                ? "This will be recorded in the history alongside the merge."
                : "Override only if the truncation is what you meant to build."
              : "Settle everything above before the merge can go through."}
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
            onClick={() => onSubmit(Boolean(frameIssue), decisions)}
            className="rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-800 hover:bg-red-50 disabled:opacity-40"
          >
            {findings.length > 0
              ? `Merge, recording ${decisions.length === 1 ? "this" : "these"}`
              : "Merge anyway"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function RuleCard({
  finding,
  reason,
  onReason,
}: {
  finding: Finding;
  reason: string;
  onReason: (text: string) => void;
}) {
  const stale = finding.suppression?.stale ?? false;
  return (
    <div className="rounded border border-red-200 bg-red-50 p-3">
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
            <em>“{finding.suppression.reason}”</em> —{" "}
            {finding.suppression.changed === "rule" ? (
              <>
                but the rule itself has changed since (pack{" "}
                <span className="font-mono">
                  {finding.suppression.pack_digest.slice(0, 8) || "unknown"}
                </span>{" "}
                →{" "}
                <span className="font-mono">
                  {finding.pack_digest.slice(0, 8)}
                </span>
                ), so that decision was taken about a different question.
              </>
            ) : (
              <>
                but the bases that decision was made about are not these bases
                any more.
              </>
            )}
          </p>
          {finding.suppression.changed === "evidence" && (
            /* Stacked and aligned: the whole question the reader has is which
               letters moved, and prose hides that. */
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
          )}
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
        value={reason}
        onChange={(event) => onReason(event.target.value)}
        placeholder="Still deliberate? Say why, against the merged sequence."
        className="mt-2 w-full rounded border border-red-300 bg-white px-1.5 py-1 text-[11px] text-slate-700 focus:border-red-500 focus:outline-none"
      />
    </div>
  );
}
