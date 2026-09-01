/** A stable colour per feature kind, so the map reads the same on every load. */

const KIND_COLORS: Record<string, string> = {
  CDS: "#4a90d9",
  gene: "#5b8def",
  promoter: "#4caf7d",
  terminator: "#d9534f",
  primer_bind: "#b07cc6",
  protein_bind: "#e08c3b",
  misc_feature: "#8c8c8c",
  misc_RNA: "#c2a03a",
  origin: "#d76b8a",
  rep_origin: "#d76b8a",
  source: "#c7ccd1",
  sig_peptide: "#7ab8c9",
};

const FALLBACK = [
  "#4a90d9", "#4caf7d", "#e08c3b", "#b07cc6",
  "#d9534f", "#c2a03a", "#7ab8c9", "#d76b8a",
];

export function featureColor(kind: string, name: string): string {
  const known = KIND_COLORS[kind];
  if (known) return known;
  let hash = 0;
  for (const char of `${kind}:${name}`) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return FALLBACK[hash % FALLBACK.length];
}
