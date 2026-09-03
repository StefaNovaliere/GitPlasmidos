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
  issue: FrameIssue;
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

/**
 * The reading-frame half of a refused merge, read as codons.
 *
 * A 409 with a message is not an explanation: three tracks - each branch, then
 * the projected merge - aligned on the codon that killed the protein.
 */
export function FrameBreakBlock({ preview, target, issue }: Props) {
  const [branch, setBranch] = useState<ConstructDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    <section>
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

      <p className="mt-4 max-w-2xl text-xs leading-relaxed text-slate-600">
        Neither branch has this problem on its own; the combination created it.
        The two edits touch no bases in common, so the coordinates merge without
        complaint — a text-level merge, or a CRDT over the sequence, reports
        success and hands back a dead protein. Only reading the result as codons
        finds it.
      </p>
    </section>
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
