/** Thin typed client for the FastAPI backend. */

import type {
  ConstructDetail,
  ConstructSummary,
  EnzymesResponse,
  History,
  ImportResult,
  OperationKind,
  Orf,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** An error carrying the backend's `detail` message so the UI can show it. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers:
        init?.body instanceof FormData
          ? undefined
          : { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(`Cannot reach the API at ${API_BASE}.`, 0);
  }

  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors
      return detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          [d.loc?.slice(1).join("."), d.msg].filter(Boolean).join(": "),
        )
        .join("; ");
    }
  } catch {
    /* fall through to the status text */
  }
  return `${response.status} ${response.statusText}`;
}

export const api = {
  listConstructs: () => request<ConstructSummary[]>("/api/constructs"),

  getConstruct: (id: string) =>
    request<ConstructDetail>(`/api/constructs/${id}`),

  createConstruct: (body: {
    name: string;
    sequence?: string;
    is_circular?: boolean;
  }) =>
    request<ConstructDetail>("/api/constructs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  importConstruct: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImportResult>("/api/constructs/import", {
      method: "POST",
      body: form,
    });
  },

  deleteConstruct: (id: string) =>
    request<void>(`/api/constructs/${id}`, { method: "DELETE" }),

  applyOperation: (
    id: string,
    kind: OperationKind,
    payload: Record<string, unknown>,
  ) =>
    request<ConstructDetail>(`/api/constructs/${id}/operations`, {
      method: "POST",
      body: JSON.stringify({ kind, payload }),
    }),

  undo: (id: string) =>
    request<ConstructDetail>(`/api/constructs/${id}/undo`, { method: "POST" }),

  redo: (id: string) =>
    request<ConstructDetail>(`/api/constructs/${id}/redo`, { method: "POST" }),

  history: (id: string) => request<History>(`/api/constructs/${id}/history`),

  enzymes: (id: string, all = false) =>
    request<EnzymesResponse>(
      `/api/constructs/${id}/enzymes${all ? "?all=true" : ""}`,
    ),

  orfs: (id: string, minLength = 300) =>
    request<{ orfs: Orf[] }>(
      `/api/constructs/${id}/orfs?min_length=${minLength}`,
    ),

  exportUrl: (id: string, format: "genbank" | "fasta") =>
    `${API_BASE}/api/constructs/${id}/export?format=${format}`,
};
