/** Display-side helpers for (possibly circular) ranges. */

import type { Feature, SelectionRange } from "./types";

/** Split `[start, end)` into the 1-2 linear pieces it actually covers. */
export function segments(
  start: number,
  end: number,
  length: number,
  isCircular: boolean,
): Array<[number, number]> {
  if (!isCircular || start < end) {
    return end > start ? [[start, end]] : [];
  }
  return ([[start, length], [0, end]] as Array<[number, number]>).filter(
    ([s, e]) => e > s,
  );
}

export function spanLength(
  start: number,
  end: number,
  length: number,
  isCircular: boolean,
): number {
  return segments(start, end, length, isCircular).reduce(
    (total, [s, e]) => total + (e - s),
    0,
  );
}

export function crossesOrigin(feature: Feature): boolean {
  return feature.start >= feature.end;
}

/** 1-based inclusive range, the way every sequence tool shows coordinates. */
export function formatRange(start: number, end: number): string {
  return `${start + 1}..${end}`;
}

export function formatSelection(
  selection: SelectionRange,
  length: number,
  isCircular: boolean,
): string {
  const size = spanLength(selection.start, selection.end, length, isCircular);
  const wraps = selection.start >= selection.end;
  return `${formatRange(selection.start, selection.end)}${
    wraps ? " (crosses origin)" : ""
  } · ${size.toLocaleString()} bp`;
}

/** A selection the toolbar can act on: non-empty and inside the sequence. */
export function isActionable(
  selection: SelectionRange | null,
  length: number,
  isCircular: boolean,
): selection is SelectionRange {
  if (!selection || length === 0) return false;
  const { start, end } = selection;
  if (start < 0 || end < 0 || start > length || end > length) return false;
  if (start === end) return false;
  if (start > end && !isCircular) return false;
  return spanLength(start, end, length, isCircular) < length;
}

const IUPAC = /^[ACGTNRYSWKMBDHV]+$/;

export function cleanSequenceInput(raw: string): string {
  return raw.replace(/[\s\d]/g, "").toUpperCase();
}

export function isValidSequence(raw: string): boolean {
  const cleaned = cleanSequenceInput(raw);
  return cleaned.length > 0 && IUPAC.test(cleaned);
}

/**
 * Predict the sequence after an edit so the viewer updates without waiting for
 * the round trip. Deliberately sequence-only: feature rebasing lives in the
 * backend's `replay()` and re-implementing it here would fork the rules. The
 * server response always replaces this preview.
 */
export function predictSequence(
  sequence: string,
  isCircular: boolean,
  kind: string,
  payload: Record<string, unknown>,
): string | null {
  const cut = (start: number, end: number): string | null => {
    if (start < end) return sequence.slice(0, start) + sequence.slice(end);
    if (!isCircular) return null;
    return sequence.slice(end, start);
  };
  switch (kind) {
    case "insert": {
      const pos = payload.pos as number;
      const seq = payload.seq as string;
      return sequence.slice(0, pos) + seq + sequence.slice(pos);
    }
    case "delete":
      return cut(payload.start as number, payload.end as number);
    case "replace": {
      const rest = cut(payload.start as number, payload.end as number);
      if (rest === null) return null;
      const start = payload.start as number;
      const end = payload.end as number;
      const at = start < end ? start : 0;
      return rest.slice(0, at) + (payload.seq as string) + rest.slice(at);
    }
    default:
      // revcomp_region, set_origin and the feature operations are left to the
      // server; they are cheap enough that the round trip is not noticeable.
      return null;
  }
}
