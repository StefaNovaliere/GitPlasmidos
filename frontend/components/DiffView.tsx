"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { formatRange } from "@/lib/sequence";
import type { ConstructDiff, Feature, SequenceSegment } from "@/lib/types";

interface Props {
  constructId: string;
  againstId: string;
  onClose: () => void;
}

export function DiffView({ constructId, againstId, onClose }: Props) {
  const [diff, setDiff] = useState<ConstructDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .diff(constructId, againstId)
      .then((d) => live && setDiff(d))
      .catch((err) =>
        live && setError(err instanceof ApiError ? err.message : String(err)),
      );
    return () => {
      live = false;
    };
  }, [constructId, againstId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {!diff && !error && (
          <p className="p-8 text-center text-sm text-slate-400">Comparing…</p>
        )}
        {error && <p className="p-8 text-center text-sm text-red-700">{error}</p>}
        {diff && <DiffBody diff={diff} onClose={onClose} />}
      </div>
    </div>
  );
}

function DiffBody({ diff, onClose }: { diff: ConstructDiff; onClose: () => void }) {
  const { sequence, features, operations, left, right } = diff;

  return (
    <>
      <header className="flex items-baseline gap-3 border-b border-slate-200 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">
          {left.name}{" "}
          <span className="font-normal text-slate-400">vs</span> {right.name}
        </h2>
        <span className="font-mono text-xs text-slate-500">
          {left.length.toLocaleString()} → {right.length.toLocaleString()} bp
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded px-2 text-slate-400 hover:bg-slate-100"
          aria-label="Close"
        >
          ×
        </button>
      </header>

      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-2 text-xs">
        {sequence.identical ? (
          <Chip tone="ok">Sequences are identical</Chip>
        ) : (
          <>
            <Chip tone="plain">
              {(sequence.identity * 100).toFixed(2)}% identity
            </Chip>
            {sequence.bases_added > 0 && (
              <Chip tone="added">+{sequence.bases_added} bp</Chip>
            )}
            {sequence.bases_removed > 0 && (
              <Chip tone="removed">−{sequence.bases_removed} bp</Chip>
            )}
          </>
        )}
        {sequence.origin_shift > 0 && (
          <Chip tone="info">
            origin moved by {sequence.origin_shift.toLocaleString()} bp
          </Chip>
        )}
        <span className="ml-auto text-slate-400">
          {operations.shared} shared edit
          {operations.shared === 1 ? "" : "s"} · {operations.left_only.length} /{" "}
          {operations.right_only.length} since the fork
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-xs">
        {sequence.origin_shift > 0 && (
          <p className="mb-4 rounded border border-sky-200 bg-sky-50 px-3 py-2 text-[11px] text-sky-900">
            One side ran <code>set_origin</code>. Coordinates were normalised
            before comparing, so a rotation shows up here rather than as every
            base having moved.
          </p>
        )}

        <Section title="Features">
          {features.added.length === 0 &&
          features.removed.length === 0 &&
          features.changed.length === 0 ? (
            <p className="text-slate-400">
              No annotation changed.{" "}
              {features.shifted.length > 0
                ? `${features.shifted.length} moved with the sequence, ${features.unchanged} untouched.`
                : `All ${features.unchanged} identical.`}
            </p>
          ) : (
            <ul className="space-y-1">
              {features.removed.map((f) => (
                <FeatureRow key={`r${f.id}`} tone="removed" feature={f} note="removed" />
              ))}
              {features.added.map((f) => (
                <FeatureRow key={`a${f.id}`} tone="added" feature={f} note="added" />
              ))}
              {features.changed.map((c) => (
                <FeatureRow
                  key={`c${c.after.id}`}
                  tone="changed"
                  feature={c.after}
                  note={c.changed_fields.join(", ")}
                  before={c.before}
                />
              ))}
              {(features.shifted.length > 0 || features.unchanged > 0) && (
                <li className="pt-1 text-slate-400">
                  {features.shifted.length > 0 && (
                    <>
                      {features.shifted.length} shifted by edits upstream
                      {features.unchanged > 0 && " · "}
                    </>
                  )}
                  {features.unchanged > 0 && `${features.unchanged} unchanged`}
                </li>
              )}
            </ul>
          )}
        </Section>

        <Section title="Sequence">
          {sequence.identical ? (
            <p className="text-slate-400">Identical, base for base.</p>
          ) : (
            <ol className="space-y-1">
              {sequence.segments.map((s, i) => (
                <SegmentRow key={i} segment={s} />
              ))}
            </ol>
          )}
        </Section>

        {(operations.left_only.length > 0 || operations.right_only.length > 0) && (
          <Section title="Edits since the fork">
            <div className="grid grid-cols-2 gap-4">
              <OperationColumn title={left.name} ops={operations.left_only} />
              <OperationColumn title={right.name} ops={operations.right_only} />
            </div>
          </Section>
        )}
      </div>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

const CHIP_TONES: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-800",
  added: "bg-emerald-100 text-emerald-800",
  removed: "bg-red-100 text-red-800",
  changed: "bg-amber-100 text-amber-900",
  info: "bg-sky-100 text-sky-900",
  plain: "bg-slate-100 text-slate-700",
};

function Chip({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span className={`rounded px-1.5 py-0.5 font-medium ${CHIP_TONES[tone]}`}>
      {children}
    </span>
  );
}

function FeatureRow({
  tone,
  feature,
  note,
  before,
}: {
  tone: "added" | "removed" | "changed";
  feature: Feature;
  note: string;
  before?: Feature;
}) {
  const marks = { added: "+", removed: "−", changed: "~" };
  return (
    <li className="flex items-baseline gap-2">
      <span className={`w-3 font-mono ${CHIP_TONES[tone].split(" ")[1]}`}>
        {marks[tone]}
      </span>
      <span className="font-medium text-slate-800">{feature.name}</span>
      <span className="text-slate-400">{feature.kind}</span>
      <span className="font-mono text-slate-500">
        {before && (before.start !== feature.start || before.end !== feature.end) ? (
          <>
            {formatRange(before.start, before.end)} →{" "}
            {formatRange(feature.start, feature.end)}
          </>
        ) : (
          formatRange(feature.start, feature.end)
        )}
      </span>
      <span className="text-[10px] text-slate-400">{note}</span>
    </li>
  );
}

function SegmentRow({ segment }: { segment: SequenceSegment }) {
  if (segment.op === "equal") {
    return (
      <li className="text-slate-400">
        <span className="font-mono">
          {(segment.left_end - segment.left_start).toLocaleString()} bp identical
        </span>{" "}
        <span className="text-[10px]">
          ({formatRange(segment.left_start, segment.left_end)})
        </span>
      </li>
    );
  }
  return (
    <li className="rounded border border-slate-200 p-2">
      <div className="mb-1 flex gap-2 text-[10px] uppercase tracking-wide text-slate-400">
        <span className="font-semibold text-slate-600">{segment.op}</span>
        <span className="font-mono normal-case">
          {formatRange(segment.left_start, segment.left_end)} →{" "}
          {formatRange(segment.right_start, segment.right_end)}
        </span>
        {segment.truncated && <span className="normal-case">(clipped)</span>}
      </div>
      {segment.left_seq && (
        <p className="break-all font-mono text-[11px] text-red-700">
          − {segment.left_seq}
          {segment.truncated && "…"}
        </p>
      )}
      {segment.right_seq && (
        <p className="break-all font-mono text-[11px] text-emerald-700">
          + {segment.right_seq}
          {segment.truncated && "…"}
        </p>
      )}
    </li>
  );
}

function OperationColumn({
  title,
  ops,
}: {
  title: string;
  ops: { id: string; kind: string }[];
}) {
  return (
    <div>
      <p className="mb-1 truncate font-medium text-slate-700">{title}</p>
      {ops.length === 0 ? (
        <p className="text-slate-400">nothing</p>
      ) : (
        <ol className="space-y-0.5">
          {ops.map((o) => (
            <li key={o.id} className="text-slate-600">
              {o.kind}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
