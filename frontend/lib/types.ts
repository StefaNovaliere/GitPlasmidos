/** Mirrors the Pydantic schemas in `backend/app/api/schemas.py`. */

export type OperationKind =
  | "insert"
  | "delete"
  | "replace"
  | "revcomp_region"
  | "add_feature"
  | "remove_feature"
  | "update_feature"
  | "set_origin"
  | "suppress_finding"
  | "unsuppress_finding";

export interface Feature {
  id: string;
  name: string;
  kind: string;
  /** 0-based, inclusive. On a circular construct `start > end` crosses the origin. */
  start: number;
  /** 0-based, exclusive. */
  end: number;
  strand: 1 | -1;
  color: string | null;
  truncated: boolean;
}

/** A coding feature whose reading frame no longer produces a protein. */
export interface FrameIssue {
  feature_id: string;
  feature_name: string;
  problem:
    | "frameshift"
    | "premature_stop"
    | "no_stop_codon"
    | "no_start_codon"
    | "too_short";
  severity: "error" | "warning" | "info";
  detail: string;
  codon: number | null;
  /** Genomic half-open span of the offending codon; null for a frameshift. */
  stop_start: number | null;
  stop_end: number | null;
  /** Genomic half-open span that still makes protein. */
  translated_start: number | null;
  translated_end: number | null;
  /** Severe enough to gate a branch merge. */
  blocking: boolean;
}

/** Where the numbers in a design rule came from. */
export interface RuleEvidence {
  citation: string;
  organism: string;
  confidence: "established" | "reported" | "heuristic";
  notes: string;
}

/**
 * The bases a rule read, hashed.
 *
 * `digest` is over the window's text alone, so an edit somewhere else moves
 * `start`/`end` without invalidating anything. Post it back verbatim to
 * suppress the finding: the engine knows what it looked at.
 */
export interface EvidenceWindow {
  algo: "sha256/1";
  digest: string;
  excerpt: string;
  /** Null once an edit erased the window the rule had read. */
  start: number | null;
  end: number | null;
}

/** Why a finding is quiet, or why it started talking again. */
export interface SuppressionState {
  reason: string;
  /** The bases under the suppression, or the rule itself, changed since. */
  stale: boolean;
  changed: "evidence" | "rule" | null;
  was: string;
  now: string;
}

/** One thing a design rule reported. Suppressed ones are marked, not dropped. */
export interface Finding {
  rule_id: string;
  title: string;
  severity: "error" | "warning" | "info";
  feature_id: string | null;
  feature_name: string;
  message: string;
  start: number | null;
  end: number | null;
  evidence: RuleEvidence;
  window: EvidenceWindow | null;
  rule_digest: string;
  suppressed: boolean;
  suppression: SuppressionState | null;
}

export interface ConstructDetail {
  id: string;
  name: string;
  description: string;
  parent_id: string | null;
  is_circular: boolean;
  sequence: string;
  features: Feature[];
  length: number;
  gc_content: number;
  warnings: string[];
  frame_issues: FrameIssue[];
  findings: Finding[];
  can_undo: boolean;
  can_redo: boolean;
  created_at: string;
  updated_at: string;
}

export interface ImportResult extends ConstructDetail {
  import_warnings: string[];
}

export interface ConstructSummary {
  id: string;
  name: string;
  description: string;
  parent_id: string | null;
  is_circular: boolean;
  length: number;
  operation_count: number;
  created_at: string;
  updated_at: string;
}

export interface OperationRecord {
  id: string;
  index: number;
  kind: OperationKind;
  payload: Record<string, unknown>;
  reverted: boolean;
  created_at: string;
}

export interface History {
  construct_id: string;
  operations: OperationRecord[];
  can_undo: boolean;
  can_redo: boolean;
}

export interface EnzymeSite {
  name: string;
  site: string;
  cut_positions: number[];
  cuts: number;
  overhang: string;
}

export interface EnzymesResponse {
  construct_id: string;
  length: number;
  is_circular: boolean;
  enzymes: EnzymeSite[];
}

export interface Orf {
  start: number;
  end: number;
  strand: 1 | -1;
  length: number;
  frame: number;
  protein: string;
}

/** A range the user picked in the viewer. `start > end` means it wraps the origin. */
export interface SelectionRange {
  start: number;
  end: number;
}

export interface BranchSummary {
  id: string;
  name: string;
  length: number;
  /** Operations this branch has added since the fork. */
  ahead: number;
  created_at: string;
  updated_at: string;
}

export interface MergeConflict {
  branch_index: number;
  kind: string;
  reason: string;
  detail: string;
}

/** What a merge would do — also the body of a 409 when it cannot proceed. */
export interface MergePreview {
  branch_id: string;
  clean: boolean;
  rebased: number;
  skipped: MergeConflict[];
  conflicts: MergeConflict[];
  new_frame_issues: FrameIssue[];
  /** What the merge would produce, present whenever the coordinates merged. */
  merged_sequence: string | null;
  merged_length: number | null;
  merged_features: Feature[];
}

export interface DiffSide {
  id: string;
  name: string;
  length: number;
}

export interface SequenceSegment {
  op: "equal" | "insert" | "delete" | "replace";
  left_start: number;
  left_end: number;
  right_start: number;
  right_end: number;
  left_seq: string;
  right_seq: string;
  truncated: boolean;
}

export interface SequenceDiff {
  identical: boolean;
  identity: number;
  bases_added: number;
  bases_removed: number;
  /** Non-zero when one side's origin was moved by `set_origin`. */
  origin_shift: number;
  segments: SequenceSegment[];
}

export interface FeatureChange {
  before: Feature;
  after: Feature;
  changed_fields: string[];
}

export interface FeatureDiff {
  added: Feature[];
  removed: Feature[];
  /** Genuinely different: renamed, restranded, or over different bases. */
  changed: FeatureChange[];
  /** Merely displaced by an indel elsewhere, still over the same bases. */
  shifted: FeatureChange[];
  unchanged: number;
}

export interface OperationsDiff {
  shared: number;
  left_only: OperationRecord[];
  right_only: OperationRecord[];
}

export interface ConstructDiff {
  left: DiffSide;
  right: DiffSide;
  relationship: "branch" | "parent" | "unrelated";
  sequence: SequenceDiff;
  features: FeatureDiff;
  operations: OperationsDiff;
}
