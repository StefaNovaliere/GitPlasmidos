"use client";

import { useEffect, useState } from "react";

import { codonWindow, readCodons, type Codon } from "@/lib/codons";
import { ApiError, api } from "@/lib/api";
import type {
  ConstructDetail,
  Feature,
  FrameIssue,
  MergePreview,
} from "@/lib/types";

/** Codons either side of the break to show. */
const WINDOW = 5;

interface Props {
  preview: MergePreview;
  target: ConstructDetail;
  onForce: () => void;
  onClose: () => void;
}

interface Track {
  label: string;
  /** Just the codons around the break. */
  codons: Codon[];
  /** How many codons the whole feature has on this branch. */
  total: number;
  /** Codon index that killed the protein, if this track is the merged one. */
  breakAt?: number;
}

function featureById(features: Feature[], id: string): Feature | undefined {
  return features.find((f) => f.id === id);
}

function trackFor(
  label: string,
  sequence: string,
  features: Feature[],
  isCircular: boolean,
  issue: FrameIssue,
  breakAt?: number,
): Track | null {
  const feature = featureById(features, issue.feature_id);
  if (!feature || !issue.codon) return null;
  const codons = readCodons(sequence, feature, isCircular);
  return {
    label,
    codons: codonWindow(codons, issue.codon, WINDOW),
    total: codons.length,
    breakAt,
  };
}

export function MergeConflictView({ preview, target, onForce, onClose }: Props) {
  const [branch, setBranch] = useState<ConstructDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const issue = preview.new_frame_issues.find((i) => i.blocking);

  useEffect(() => {
    let live = true;
    api
      .getConstruct(preview.branch_id)
      .then((c) => live && setBranch(c))
      .catch((err) =>
        live && setError(err instanceof ApiError ? err.message : String(err)),
      );
    return () => {
      live = false;
    };
  }, [preview.branch_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!issue) return null;

  const tracks: Track[] = [];
  const merged = trackFor(
    "merged",
    preview.merged_sequence ?? "",
    preview.merged_features,
    target.is_circular,
    issue,
    issue.codon ?? undefined,
  );
  const targetTrack = trackFor(
    target.name,
    target.sequence,
    target.features,
    target.is_circular,
    issue,
  );
  if (targetTrack) tracks.push(targetTrack);
  if (branch) {
    const branchTrack = trackFor(
      branch.name,
      branch.sequence,
      branch.features,
      branch.is_circular,
      issue,
    );
    if (branchTrack) tracks.push(branchTrack);
  }
  if (merged) tracks.push(merged);

  const survivors =
    issue.translated_start !== null && issue.translated_end !== null
      ? (issue.translated_end - issue.translated_start) / 3
      : null;

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
          <p className="text-xs text-slate-500">
            The coordinates merge cleanly. The protein does not.
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

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-sm font-medium text-slate-900">
              {issue.feature_name}
            </span>
            <span className="rounded bg-red-100 px-1.5 py-0.5 text-[11px] font-medium text-red-800">
              stop at codon {issue.codon}
            </span>
            {survivors !== null && merged && (
              <span className="font-mono text-xs text-slate-500">
                {survivors} of {merged.total - 1} residues survive
              </span>
            )}
          </div>

          {error && <p className="mt-3 text-xs text-red-700">{error}</p>}

          <div className="mt-4 overflow-x-auto">
            <div className="inline-block min-w-full space-y-3">
              {tracks.map((track) => (
                <CodonTrack key={track.label} track={track} />
              ))}
              {!branch && !error && (
                <p className="text-xs text-slate-400">Loading the branch…</p>
              )}
            </div>
          </div>

          <p className="mt-5 max-w-2xl text-xs leading-relaxed text-slate-600">
            Neither branch has this problem on its own; the combination created
            it. The two edits touch no bases in common, so the coordinates
            merge without complaint — a text-level merge, or a CRDT over the
            sequence, reports success and hands back a dead protein. Only
            reading the result as codons finds it.
          </p>
        </div>

        <footer className="flex items-center gap-2 border-t border-slate-200 px-5 py-3">
          <p className="flex-1 text-[11px] text-slate-500">
            Override only if the truncation is what you meant to build.
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
            onClick={onForce}
            className="rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-800 hover:bg-red-50"
          >
            Merge anyway
          </button>
        </footer>
      </div>
    </div>
  );
}

function CodonTrack({ track }: { track: Track }) {
  return (
    <div className="flex items-start gap-3">
      <span className="w-40 shrink-0 truncate pt-1 text-right text-xs text-slate-500">
        {track.label}
      </span>
      <div className="flex gap-1">
        {track.codons.map((codon) => {
          const broken = track.breakAt === codon.index;
          return (
            <div
              key={codon.index}
              className={`rounded px-1 py-0.5 text-center font-mono ${
                broken
                  ? "bg-red-100 text-red-900 ring-1 ring-red-300"
                  : codon.aminoAcid === "*"
                    ? "text-slate-400"
                    : "text-slate-700"
              }`}
              title={`codon ${codon.index}`}
            >
              <div className="text-[11px] leading-tight">{codon.bases}</div>
              <div
                className={`text-[11px] font-semibold leading-tight ${
                  broken ? "text-red-700" : "text-slate-400"
                }`}
              >
                {codon.aminoAcid}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
