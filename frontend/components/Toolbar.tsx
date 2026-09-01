"use client";

import { useState } from "react";

import {
  cleanSequenceInput,
  formatSelection,
  isActionable,
  isValidSequence,
} from "@/lib/sequence";
import type { ConstructDetail, SelectionRange } from "@/lib/types";

const FEATURE_KINDS = [
  "CDS",
  "promoter",
  "terminator",
  "primer_bind",
  "misc_feature",
] as const;

interface Props {
  construct: ConstructDetail;
  selection: SelectionRange | null;
  cursor: number | null;
  busy: boolean;
  onDelete: (selection: SelectionRange) => void;
  onRevComp: (selection: SelectionRange) => void;
  onAnnotate: (
    selection: SelectionRange,
    name: string,
    kind: string,
    strand: 1 | -1,
  ) => void;
  onInsert: (pos: number, seq: string) => void;
  onSetOrigin: (pos: number) => void;
}

export function Toolbar({
  construct,
  selection,
  cursor,
  busy,
  onDelete,
  onRevComp,
  onAnnotate,
  onInsert,
  onSetOrigin,
}: Props) {
  const [insertSeq, setInsertSeq] = useState("");
  const [annotating, setAnnotating] = useState(false);
  const [featureName, setFeatureName] = useState("");
  const [featureKind, setFeatureKind] = useState<string>("misc_feature");
  const [featureStrand, setFeatureStrand] = useState<1 | -1>(1);

  const { length, is_circular: isCircular } = construct;
  const hasSelection = isActionable(selection, length, isCircular);
  const insertPos = cursor ?? selection?.start ?? null;
  const canInsert =
    !busy && insertPos !== null && isValidSequence(insertSeq);

  const submitAnnotation = () => {
    if (!hasSelection) return;
    const name = featureName.trim();
    if (!name) return;
    onAnnotate(selection, name, featureKind, featureStrand);
    setFeatureName("");
    setAnnotating(false);
  };

  const buttonClass =
    "rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
      <button
        type="button"
        className={buttonClass}
        disabled={!hasSelection || busy}
        onClick={() => hasSelection && onDelete(selection)}
        title="Delete the selected range"
      >
        Delete
      </button>

      <button
        type="button"
        className={buttonClass}
        disabled={!hasSelection || busy}
        onClick={() => hasSelection && onRevComp(selection)}
        title="Reverse complement the selected range"
      >
        Reverse complement
      </button>

      <div className="relative">
        <button
          type="button"
          className={buttonClass}
          disabled={!hasSelection || busy}
          onClick={() => setAnnotating((open) => !open)}
        >
          Annotate as…
        </button>

        {annotating && hasSelection && (
          <div className="absolute left-0 top-full z-30 mt-1 w-72 rounded-md border border-slate-200 bg-white p-3 shadow-lg">
            <label className="block text-[11px] font-medium text-slate-500">
              Name
              <input
                autoFocus
                value={featureName}
                onChange={(e) => setFeatureName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitAnnotation();
                  if (e.key === "Escape") setAnnotating(false);
                }}
                placeholder="e.g. eGFP"
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-xs text-slate-800"
              />
            </label>

            <div className="mt-2 flex gap-2">
              <label className="flex-1 text-[11px] font-medium text-slate-500">
                Type
                <select
                  value={featureKind}
                  onChange={(e) => setFeatureKind(e.target.value)}
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-xs text-slate-800"
                >
                  {FEATURE_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {kind}
                    </option>
                  ))}
                </select>
              </label>
              <label className="w-24 text-[11px] font-medium text-slate-500">
                Strand
                <select
                  value={featureStrand}
                  onChange={(e) =>
                    setFeatureStrand(Number(e.target.value) as 1 | -1)
                  }
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-xs text-slate-800"
                >
                  <option value={1}>+ (→)</option>
                  <option value={-1}>− (←)</option>
                </select>
              </label>
            </div>

            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setAnnotating(false)}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submitAnnotation}
                disabled={!featureName.trim()}
                className="rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-40"
              >
                Add feature
              </button>
            </div>
          </div>
        )}
      </div>

      <button
        type="button"
        className={buttonClass}
        disabled={!hasSelection || busy || !isCircular}
        onClick={() => hasSelection && onSetOrigin(selection.start)}
        title={
          isCircular
            ? "Rotate the plasmid so the selection starts at position 1"
            : "Only meaningful on a circular construct"
        }
      >
        Set origin here
      </button>

      <div className="mx-1 h-5 w-px bg-slate-200" aria-hidden />

      <div className="flex items-center gap-1.5">
        <input
          value={insertSeq}
          onChange={(e) => setInsertSeq(cleanSequenceInput(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter" && canInsert && insertPos !== null) {
              onInsert(insertPos, insertSeq);
              setInsertSeq("");
            }
          }}
          placeholder="ACGT… to insert"
          spellCheck={false}
          className={`w-52 rounded border px-2 py-1 font-mono text-xs ${
            insertSeq && !isValidSequence(insertSeq)
              ? "border-red-400 text-red-700"
              : "border-slate-300 text-slate-800"
          }`}
        />
        <button
          type="button"
          className={buttonClass}
          disabled={!canInsert}
          onClick={() => {
            if (insertPos === null) return;
            onInsert(insertPos, insertSeq);
            setInsertSeq("");
          }}
          title={
            insertPos === null
              ? "Click in the sequence to place the cursor first"
              : `Insert at position ${insertPos + 1}`
          }
        >
          Insert{insertPos !== null ? ` at ${insertPos + 1}` : ""}
        </button>
      </div>

      <div className="ml-auto text-xs text-slate-500">
        {hasSelection ? (
          <span className="font-mono">
            {formatSelection(selection, length, isCircular)}
          </span>
        ) : (
          <span className="text-slate-400">
            Drag on the viewer to select a range
          </span>
        )}
      </div>
    </div>
  );
}
