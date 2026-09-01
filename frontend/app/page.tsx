"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useToasts } from "@/components/Toasts";
import { ApiError, api } from "@/lib/api";
import type { ConstructSummary } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const { push, pushAll } = useToasts();
  const [constructs, setConstructs] = useState<ConstructSummary[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    try {
      setConstructs(await api.listConstructs());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const importFile = async (file: File) => {
    setBusy(true);
    try {
      const imported = await api.importConstruct(file);
      if (imported.import_warnings.length) {
        pushAll(imported.import_warnings, "warning");
      }
      router.push(`/constructs/${imported.id}`);
    } catch (err) {
      push(err instanceof ApiError ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const createBlank = async () => {
    setBusy(true);
    try {
      const created = await api.createConstruct({
        name: "Untitled construct",
        sequence: "",
        is_circular: true,
      });
      router.push(`/constructs/${created.id}`);
    } catch (err) {
      push(err instanceof ApiError ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex h-full max-w-3xl flex-col gap-6 px-6 py-10">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">visorADN</h1>
        <p className="mt-1 text-sm text-slate-500">
          A plasmid editor whose current state is derived, never stored — every
          edit is a reversible entry in an append-only log.
        </p>
      </header>

      <div className="flex items-center gap-2">
        <input
          ref={fileInput}
          type="file"
          accept=".fasta,.fa,.fna,.gb,.gbk,.genbank,.txt"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void importFile(file);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
          className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
        >
          Import FASTA / GenBank
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void createBlank()}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-40"
        >
          New empty construct
        </button>
      </div>

      {error && (
        <p className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {error}
        </p>
      )}

      <section className="min-h-0 flex-1 overflow-y-auto rounded border border-slate-200 bg-white">
        {constructs === null && (
          <p className="px-4 py-6 text-sm text-slate-400">Loading…</p>
        )}
        {constructs?.length === 0 && (
          <p className="px-4 py-6 text-sm text-slate-400">
            Nothing here yet. Import a GenBank file to get started.
          </p>
        )}
        <ul className="divide-y divide-slate-100">
          {constructs?.map((construct) => (
            <li key={construct.id}>
              <Link
                href={`/constructs/${construct.id}`}
                className="flex items-baseline gap-3 px-4 py-2.5 hover:bg-slate-50"
              >
                <span className="flex-1 truncate text-sm font-medium text-slate-800">
                  {construct.name}
                </span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                  {construct.is_circular ? "circular" : "linear"}
                </span>
                <span className="font-mono text-xs text-slate-500">
                  {construct.length.toLocaleString()} bp
                </span>
                <span className="text-xs text-slate-400">
                  {construct.operation_count} edit
                  {construct.operation_count === 1 ? "" : "s"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
