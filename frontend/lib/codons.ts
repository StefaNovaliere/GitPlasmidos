/**
 * Reading a coding feature codon by codon, for the close-up views.
 *
 * The genetic code below is NCBI translation table 1, the same one the backend
 * translates with. Duplicating it here is deliberate and safe: it is a
 * universal constant rather than project logic, and every *fact* the UI states
 * — which codon broke, how long the protein is — still comes from the backend.
 * This is only used to letter the bases on screen.
 */

import { segments } from "./sequence";
import type { Feature } from "./types";

const BASES = "TCAG";
const AMINO_ACIDS =
  "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG";

const CODON_TABLE: Record<string, string> = {};
for (let i = 0; i < 4; i++) {
  for (let j = 0; j < 4; j++) {
    for (let k = 0; k < 4; k++) {
      CODON_TABLE[BASES[i] + BASES[j] + BASES[k]] =
        AMINO_ACIDS[i * 16 + j * 4 + k];
    }
  }
}

const COMPLEMENT: Record<string, string> = {
  A: "T", C: "G", G: "C", T: "A", N: "N",
  R: "Y", Y: "R", S: "S", W: "W", K: "M", M: "K",
  B: "V", V: "B", D: "H", H: "D",
};

export function reverseComplement(seq: string): string {
  let out = "";
  for (let i = seq.length - 1; i >= 0; i--) out += COMPLEMENT[seq[i]] ?? "N";
  return out;
}

/** `*` for a stop, `X` for anything with an ambiguous base in it. */
export function translateCodon(codon: string): string {
  return CODON_TABLE[codon.toUpperCase()] ?? "X";
}

/** Genomic indices a feature covers, ascending, following the wraparound. */
export function coveredPositions(
  feature: Feature,
  length: number,
  isCircular: boolean,
): number[] {
  const positions: number[] = [];
  for (const [start, end] of segments(
    feature.start,
    feature.end,
    length,
    isCircular,
  )) {
    for (let p = start; p < end; p++) positions.push(p);
  }
  return positions;
}

/**
 * The same indices in *reading* order — reversed for a minus-strand feature.
 * Mirrors `analysis.cds_positions` on the backend. Use this to map a codon
 * number back onto the map, not to build the coding sequence: the bases still
 * have to be complemented, and doing both here reverses twice.
 */
export function cdsPositions(
  feature: Feature,
  length: number,
  isCircular: boolean,
): number[] {
  const positions = coveredPositions(feature, length, isCircular);
  return feature.strand === -1 ? positions.reverse() : positions;
}

export interface Codon {
  /** 1-based, the way a biologist counts. */
  index: number;
  bases: string;
  aminoAcid: string;
}

/** Every codon of a coding feature, in reading order. */
export function readCodons(
  sequence: string,
  feature: Feature,
  isCircular: boolean,
): Codon[] {
  // Ascending genomic order first, then reverse complement it as a whole —
  // exactly what `analysis.coding_sequence` does on the backend.
  const span = coveredPositions(feature, sequence.length, isCircular)
    .map((p) => sequence[p])
    .join("");
  const bases = feature.strand === -1 ? reverseComplement(span) : span;

  const codons: Codon[] = [];
  for (let i = 0; i + 3 <= bases.length; i += 3) {
    const triplet = bases.slice(i, i + 3);
    codons.push({
      index: i / 3 + 1,
      bases: triplet,
      aminoAcid: translateCodon(triplet),
    });
  }
  return codons;
}

/** The codons around `centre` (1-based), clamped to the ends of the feature. */
export function codonWindow(
  codons: Codon[],
  centre: number,
  radius: number,
): Codon[] {
  const from = Math.max(0, centre - 1 - radius);
  return codons.slice(from, centre - 1 + radius + 1);
}
