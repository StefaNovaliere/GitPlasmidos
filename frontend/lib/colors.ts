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


/** The same colour at a lower alpha, for annotations that are inert. */
export function translucent(color: string, alpha: number): string {
  const hex = color.trim();
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return color;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
