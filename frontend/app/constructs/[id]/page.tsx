"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { BranchMenu } from "@/components/BranchMenu";
import { EnzymePanel } from "@/components/EnzymePanel";
import { FeatureList } from "@/components/FeatureList";
import { FindingsPanel } from "@/components/FindingsPanel";
import { HistoryPanel } from "@/components/HistoryPanel";
import { SeqVizPane } from "@/components/SeqVizPane";
import { Toolbar } from "@/components/Toolbar";
import { useToasts } from "@/components/Toasts";
import { api } from "@/lib/api";
import { isActionable } from "@/lib/sequence";
import { useConstruct } from "@/lib/useConstruct";
import type { Feature, Finding, FrameIssue, SelectionRange } from "@/lib/types";

export default function ConstructPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { push, pushAll } = useToasts();

  const onWarnings = useCallback(
    (messages: string[]) => pushAll(messages, "warning"),
    [pushAll],
  );
  const onError = useCallback(
    (message: string) => push(message, "error"),
    [push],
  );

  const { construct, history, loading, pending, error, apply, undo, redo, reload } =
    useConstruct(id, onWarnings, onError);

  const [selection, setSelection] = useState<SelectionRange | null>(null);
  const [selectedFeatureId, setSelectedFeatureId] = useState<string | null>(null);
  const [enzymes, setEnzymes] = useState<string[]>([]);
  const [focus, setFocus] = useState<SelectionRange | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  // Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      event.preventDefault();
      void (event.shiftKey ? redo() : undo());
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undo, redo]);

  const handleViewerSelection = useCallback(
    (range: SelectionRange | null, annotationName: string | null) => {
      setSelection(range);
      if (!annotationName || !construct) {
        setSelectedFeatureId(null);
        return;
      }
      const match = construct.features.find(
        (f) =>
          f.name === annotationName &&
          range !== null &&
          f.start === range.start &&
          f.end === range.end,
      );
      setSelectedFeatureId(match?.id ?? null);
    },
    [construct],
  );

  const selectFeature = useCallback((feature: Feature) => {
    setSelection({ start: feature.start, end: feature.end });
    setSelectedFeatureId(feature.id);
  }, []);

  /** Scroll to a span and select it, falling back to the feature it is about. */
  const goTo = useCallback(
    (span: SelectionRange | null, featureId: string | null) => {
      if (!construct) return;
      const feature = construct.features.find((f) => f.id === featureId);
      const target =
        span ?? (feature ? { start: feature.start, end: feature.end } : null);
      if (!target) return;
      setSelection(target);
      setSelectedFeatureId(feature?.id ?? null);
      setFocus(target);
      // Hand selection back to the viewer once it has scrolled, so dragging
      // keeps working.
      window.setTimeout(() => setFocus(null), 600);
    },
    [construct],
  );

  /**
   * A premature stop has one codon to blame, so go to it; a frameshift does
   * not - the frame is wrong from the indel onwards - so go to the feature it
   * ruined.
   */
  const goToIssue = useCallback(
    (issue: FrameIssue) =>
      goTo(
        issue.stop_start !== null && issue.stop_end !== null
          ? { start: issue.stop_start, end: issue.stop_end }
          : null,
        issue.feature_id,
      ),
    [goTo],
  );

  const goToFinding = useCallback(
    (finding: Finding) =>
      goTo(
        finding.start !== null && finding.end !== null
          ? { start: finding.start, end: finding.end }
          : null,
        finding.feature_id,
      ),
    [goTo],
  );

  /**
   * Silencing a finding is an edit like any other: it goes in the log, so it
   * has an author, an undo, a place in the diff and a defined behaviour under
   * merge. The evidence goes back exactly as the engine computed it - the
   * client has no business deciding which bases the rule read.
   */
  const suppressFinding = useCallback(
    (finding: Finding, reason: string) => {
      if (!finding.feature_id || !finding.window) return;
      void apply("suppress_finding", {
        rule_id: finding.rule_id,
        feature_id: finding.feature_id,
        reason,
        window: finding.window,
        rule_digest: finding.rule_digest,
      });
    },
    [apply],
  );

  const unsuppressFinding = useCallback(
    (finding: Finding) => {
      if (!finding.feature_id) return;
      void apply("unsuppress_finding", {
        rule_id: finding.rule_id,
        feature_id: finding.feature_id,
      });
    },
    [apply],
  );

  const runOperation = useCallback(
    async (kind: Parameters<typeof apply>[0], payload: Record<string, unknown>) => {
      const ok = await apply(kind, payload);
      if (ok) {
        setSelection(null);
        setSelectedFeatureId(null);
      }
    },
    [apply],
  );

  const cursor = useMemo(
    () => (selection && selection.start === selection.end ? selection.start : null),
    [selection],
  );

  const blockingIssues = construct?.frame_issues.filter((i) => i.blocking) ?? [];
  const findings = construct?.findings ?? [];
  const ruleErrors = findings.filter((f) => f.severity === "error" && !f.suppressed);
  const suppressedCount = findings.filter((f) => f.suppressed).length;

  const actionableSelection =
    construct && isActionable(selection, construct.length, construct.is_circular)
      ? selection
      : null;

  if (loading && !construct) {
    return <Centered>Loading construct…</Centered>;
  }
  if (error && !construct) {
    return (
      <Centered>
        <p className="text-red-700">{error}</p>
        <Link href="/" className="mt-3 text-xs text-slate-500 underline">
          Back to all constructs
        </Link>
      </Centered>
    );
  }
  if (!construct) return <Centered>Not found.</Centered>;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
        <Link
          href="/"
          className="text-xs text-slate-400 hover:text-slate-700"
          title="All constructs"
        >
          ←
        </Link>
        <h1 className="text-sm font-semibold text-slate-900">{construct.name}</h1>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
          {construct.is_circular ? "circular" : "linear"}
        </span>
        <span className="font-mono text-xs text-slate-500">
          {construct.length.toLocaleString()} bp
        </span>
        <span className="font-mono text-xs text-slate-500">
          GC {(construct.gc_content * 100).toFixed(1)}%
        </span>
        {blockingIssues.length > 0 && (
          <button
            type="button"
            onClick={() => goToIssue(blockingIssues[0])}
            title={
              "DNA is read three letters at a time, and each triplet is one " +
              "amino acid. These features no longer read as the protein they " +
              "are annotated as:\n\n" +
              blockingIssues
                .map((i) => `${i.feature_name} — ${i.detail}`)
                .join("\n") +
              "\n\nClick to go there."
            }
            className="rounded bg-red-100 px-1.5 py-0.5 text-[11px] font-medium text-red-800 hover:bg-red-200"
          >
            {blockingIssues.length} broken reading frame
            {blockingIssues.length === 1 ? "" : "s"} →
          </button>
        )}
        {ruleErrors.length > 0 && (
          <button
            type="button"
            onClick={() => goToFinding(ruleErrors[0])}
            title={
              "Design rules the construct breaks:\n\n" +
              ruleErrors.map((f) => `${f.message} (${f.rule_id})`).join("\n") +
              (suppressedCount > 0
                ? `\n\n${suppressedCount} other finding${
                    suppressedCount === 1 ? " is" : "s are"
                  } suppressed.`
                : "") +
              "\n\nClick to go there."
            }
            className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 hover:bg-amber-200"
          >
            {ruleErrors.length} rule error{ruleErrors.length === 1 ? "" : "s"} →
          </button>
        )}
        {pending && (
          <span className="text-xs text-slate-400" role="status">
            saving…
          </span>
        )}
        <div className="ml-auto flex items-center gap-2 text-xs">
          <BranchMenu
            construct={construct}
            busy={pending}
            onMerged={() => {
              push("Branch merged.", "info");
              void reload();
            }}
            onBranchCreated={(id) => router.push(`/constructs/${id}`)}
            onError={onError}
          />
          <a
            href={api.exportUrl(construct.id, "genbank")}
            className="rounded border border-slate-300 px-2 py-1 text-slate-700 hover:bg-slate-100"
          >
            Export GenBank
          </a>
          <a
            href={api.exportUrl(construct.id, "fasta")}
            className="rounded border border-slate-300 px-2 py-1 text-slate-700 hover:bg-slate-100"
          >
            FASTA
          </a>
        </div>
      </header>

      <Toolbar
        construct={construct}
        selection={actionableSelection}
        cursor={cursor}
        busy={pending}
        onDelete={(range) =>
          void runOperation("delete", { start: range.start, end: range.end })
        }
        onRevComp={(range) =>
          void runOperation("revcomp_region", {
            start: range.start,
            end: range.end,
          })
        }
        onAnnotate={(range, name, kind, strand) =>
          void runOperation("add_feature", {
            feature: {
              id: crypto.randomUUID(),
              name,
              kind,
              start: range.start,
              end: range.end,
              strand,
            },
          })
        }
        onInsert={(pos, seq) => void runOperation("insert", { pos, seq })}
        onSetOrigin={(pos) => void runOperation("set_origin", { pos })}
      />

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-80 shrink-0 flex-col border-r border-slate-200 bg-white">
          <FeatureList
            features={construct.features}
            length={construct.length}
            isCircular={construct.is_circular}
            frameIssues={construct.frame_issues}
            selectedId={selectedFeatureId}
            onSelect={selectFeature}
            onRemove={(feature) =>
              void runOperation("remove_feature", { feature_id: feature.id })
            }
          />
          <FindingsPanel
            findings={findings}
            busy={pending}
            onSelect={goToFinding}
            onSuppress={suppressFinding}
            onUnsuppress={unsuppressFinding}
          />
          <HistoryPanel
            history={history}
            canUndo={construct.can_undo}
            canRedo={construct.can_redo}
            busy={pending}
            onUndo={() => void undo()}
            onRedo={() => void redo()}
          />
        </aside>

        <main className="min-w-0 flex-1 bg-white">
          <SeqVizPane
            construct={construct}
            enzymes={enzymes}
            focus={focus}
            onSelection={handleViewerSelection}
          />
        </main>

        <EnzymePanel
          constructId={construct.id}
          revision={construct.updated_at}
          selected={enzymes}
          onChange={setEnzymes}
          collapsed={panelCollapsed}
          onToggleCollapsed={() => setPanelCollapsed((c) => !c)}
        />
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center text-sm text-slate-500">
      {children}
    </div>
  );
}
