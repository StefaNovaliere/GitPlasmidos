"use client";

import { useState } from "react";

import type { Finding, RulePack } from "@/lib/types";

interface Props {
  findings: Finding[];
  pack: RulePack;
  busy: boolean;
  onSelect: (finding: Finding) => void;
  onSuppress: (finding: Finding, reason: string) => void;
  onUnsuppress: (finding: Finding) => void;
}

const DOT: Record<Finding["severity"], string> = {
  error: "bg-red-500",
  warning: "bg-amber-500",
  info: "bg-sky-500",
};

function keyOf(finding: Finding): string {
  return `${finding.rule_id}:${finding.feature_id ?? ""}:${finding.start ?? ""}`;
}

/** The literature behind a rule, as a tooltip. A threshold with no citation is folklore. */
function provenance(finding: Finding): string {
  const { citation, organism, confidence, notes } = finding.evidence;
  return [
    finding.title,
    `${citation} — ${organism} (${confidence})`,
    notes.trim(),
  ]
    .filter(Boolean)
    .join("\n\n");
}

/**
 * Design-rule findings, with the suppressed ones still on screen.
 *
 * Two rules of the panel, both learned the hard way by every linter:
 *
 * * a suppressed finding is dimmed and counted, never hidden — hidden ones rot;
 * * a suppression whose evidence moved on says so, quoting both readings, and
 *   hands the decision back rather than deciding on its own.
 */
export function FindingsPanel({
  findings,
  pack,
  busy,
  onSelect,
  onSuppress,
  onUnsuppress,
}: Props) {
  const [drafting, setDrafting] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const suppressed = findings.filter((f) => f.suppressed).length;
  const live = findings.length - suppressed;

  const startDraft = (finding: Finding) => {
    setDrafting(keyOf(finding));
    setReason(finding.suppression?.reason ?? "");
  };

  const commit = (finding: Finding) => {
    const text = reason.trim();
    if (text.length < 3) return;
    onSuppress(finding, text);
    setDrafting(null);
    setReason("");
  };

  return (
    <section className="flex max-h-[40%] min-h-0 flex-col border-t border-slate-200">
      <header className="px-3 py-2">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Design rules
          </h2>
          <span className="font-mono text-[11px] text-slate-400">
            {live} finding{live === 1 ? "" : "s"}
            {suppressed > 0 && `, ${suppressed} suppressed`}
          </span>
        </div>
        {/* Which rules judged this. A gate that can change on the server
            without saying so stops being trusted. */}
        <p
          title={
            `Rule pack ${pack.version} · ${pack.digest}\n` +
            `${pack.rules} rule${pack.rules === 1 ? "" : "s"} loaded` +
            (pack.errors.length
              ? `\n\nRejected:\n${pack.errors.join("\n")}`
              : "")
          }
          className={`mt-0.5 font-mono text-[10px] ${
            pack.errors.length ? "text-amber-700" : "text-slate-400"
          }`}
        >
          pack {pack.digest.slice(0, 6)} · {pack.rules} rule
          {pack.rules === 1 ? "" : "s"}
          {pack.errors.length > 0 &&
            `, ${pack.errors.length} rejected`}
        </p>
      </header>

      <ul className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {findings.length === 0 && (
          <li className="px-2 py-4 text-center text-xs text-slate-400">
            Nothing to report — every rule in the pack passes.
          </li>
        )}
        {findings.map((finding) => {
          const key = keyOf(finding);
          const stale = finding.suppression?.stale ?? false;
          return (
            <li
              key={key}
              className={`rounded px-2 py-1.5 ${
                finding.suppressed ? "opacity-55" : ""
              }`}
            >
              <div className="flex items-start gap-2">
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                    DOT[finding.severity]
                  }`}
                  aria-hidden
                />
                <button
                  type="button"
                  onClick={() => onSelect(finding)}
                  title={provenance(finding)}
                  className="min-w-0 flex-1 text-left text-xs text-slate-700 hover:text-slate-900"
                >
                  <span className={finding.suppressed ? "line-through" : ""}>
                    {finding.message}
                  </span>
                  <span className="ml-1 font-mono text-[10px] text-slate-400">
                    {finding.rule_id}
                  </span>
                </button>
                {finding.suppressed ? (
                  <button
                    type="button"
                    onClick={() => onUnsuppress(finding)}
                    disabled={busy}
                    title={`Suppressed: ${finding.suppression?.reason ?? ""}`}
                    className="shrink-0 text-[11px] text-slate-500 underline hover:text-slate-800 disabled:opacity-40"
                  >
                    restore
                  </button>
                ) : (
                  drafting !== key && (
                    <button
                      type="button"
                      onClick={() => startDraft(finding)}
                      disabled={busy || !finding.window || !finding.feature_id}
                      title="Record a decision not to act on this"
                      className="shrink-0 text-[11px] text-slate-500 underline hover:text-slate-800 disabled:opacity-40"
                    >
                      suppress
                    </button>
                  )
                )}
              </div>

              {finding.suppressed && finding.suppression && (
                <p className="mt-0.5 pl-3.5 text-[11px] italic text-slate-500">
                  {finding.suppression.reason}
                </p>
              )}

              {stale && finding.suppression && (
                <div className="mt-1 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-900">
                  {finding.suppression.changed === "rule" ? (
                    <p>
                      The rule changed since this was suppressed — pack{" "}
                      <span className="font-mono">
                        {finding.suppression.pack_digest.slice(0, 6) || "unknown"}
                      </span>{" "}
                      →{" "}
                      <span className="font-mono">
                        {finding.pack_digest.slice(0, 6)}
                      </span>
                      .
                    </p>
                  ) : (
                    <p>
                      Suppressed when this region read{" "}
                      <span className="font-mono">{finding.suppression.was}</span>;
                      it now reads{" "}
                      <span className="font-mono">{finding.suppression.now}</span>.
                    </p>
                  )}
                  <p className="mt-0.5 italic">“{finding.suppression.reason}”</p>
                  <div className="mt-1 flex gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        onSuppress(finding, finding.suppression?.reason ?? "")
                      }
                      disabled={busy}
                      className="underline hover:text-amber-950 disabled:opacity-40"
                    >
                      still deliberate
                    </button>
                    <button
                      type="button"
                      onClick={() => onUnsuppress(finding)}
                      disabled={busy}
                      className="underline hover:text-amber-950 disabled:opacity-40"
                    >
                      drop the suppression
                    </button>
                  </div>
                </div>
              )}

              {drafting === key && (
                <div className="mt-1 pl-3.5">
                  <textarea
                    autoFocus
                    rows={2}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Why is this deliberate? Whoever reads the history has only this line."
                    className="w-full rounded border border-slate-300 px-1.5 py-1 text-[11px] text-slate-700 focus:border-slate-500 focus:outline-none"
                  />
                  <div className="mt-1 flex gap-2 text-[11px]">
                    <button
                      type="button"
                      onClick={() => commit(finding)}
                      disabled={busy || reason.trim().length < 3}
                      className="rounded bg-slate-800 px-2 py-0.5 text-white hover:bg-slate-700 disabled:opacity-40"
                    >
                      Suppress
                    </button>
                    <button
                      type="button"
                      onClick={() => setDrafting(null)}
                      className="text-slate-500 underline hover:text-slate-800"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
