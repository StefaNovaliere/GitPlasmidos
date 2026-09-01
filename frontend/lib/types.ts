/** Mirrors the Pydantic schemas in `backend/app/api/schemas.py`. */

export type OperationKind =
  | "insert"
  | "delete"
  | "replace"
  | "revcomp_region"
  | "add_feature"
  | "remove_feature"
  | "update_feature"
  | "set_origin";

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

export interface ConstructDetail {
  id: string;
  name: string;
  is_circular: boolean;
  sequence: string;
  features: Feature[];
  length: number;
  gc_content: number;
  warnings: string[];
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
