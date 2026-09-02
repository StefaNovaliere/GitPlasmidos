"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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

  const remove = async (construct: ConstructSummary) => {
    const branches = (constructs ?? []).filter(
      (c) => c.parent_id === construct.id,
    );
    const warning = branches.length
      ? `Delete “${construct.name}”? Its ${branches.length} branch${
          branches.length === 1 ? "" : "es"
        } will be left without a parent.`
      : `Delete “${construct.name}”? Its edit history goes with it.`;
    if (!window.confirm(warning)) return;

    setBusy(true);
    try {
      await api.deleteConstruct(construct.id);
      await reload();
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

  // Each root followed by its branches, oldest root first so the listing
  // reads in the same order as the numbered walkthrough above.
  const ordered = useMemo(() => {
    if (!constructs) return null;
    const isRoot = (c: ConstructSummary) =>
      !c.parent_id || !constructs.some((p) => p.id === c.parent_id);
    const roots = constructs
      .filter(isRoot)
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    return roots.flatMap((root) => [
      root,
      ...constructs.filter((c) => c.parent_id === root.id),
    ]);
  }, [constructs]);

  const seeded = constructs?.some((c) => c.name.startsWith("pUC19")) ?? false;

  return (
    <main className="mx-auto flex h-full max-w-3xl flex-col gap-6 overflow-y-auto px-6 py-10">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">visorADN</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
          A plasmid editor for circular DNA. Import a GenBank file, edit the
          sequence, and undo any of it — because the current state is never
          stored.
        </p>
      </header>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          How it works
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          A construct persists three things: the sequence as imported, its
          features as imported, and an append-only log of edits. Everything you
          see is derived by replaying that log. Undo is a boolean on one
          operation, history is auditable by construction, and branches can be
          merged by rebasing one log onto another.
        </p>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          Which is what makes the interesting check possible: a merge that
          applies cleanly at the coordinate level can still destroy a protein,
          and the same engine that moves coordinates can notice.
        </p>
      </section>

      {seeded && (
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Worth a look
          </h2>
          <ol className="space-y-1.5 text-sm text-slate-600">
            <li className="flex gap-2">
              <span className="text-slate-400">1.</span>
              <span>
                Open <strong className="font-medium text-slate-800">pUC19</strong>{" "}
                — the circular map, its 18 features, and the single cutters in
                the enzyme panel, all derived from an empty log.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-slate-400">2.</span>
              <span>
                On pUC19, open <em>Branches → Compare</em> against{" "}
                <strong className="font-medium text-slate-800">MCS swap</strong>{" "}
                to diff them. Note how features that merely moved are reported
                apart from the ones that actually changed.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-slate-400">3.</span>
              <span>
                Open{" "}
                <strong className="font-medium text-slate-800">AmpR +Phe</strong>{" "}
                and try <em>Branches → Merge</em>. Both branches add one amino
                acid to the resistance gene, both proteins are full length, and
                the merge is refused — together they read as a stop codon.
              </span>
            </li>
          </ol>
        </section>
      )}

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

      <section className="rounded-lg border border-slate-200 bg-white">
        {ordered === null && (
          <p className="px-4 py-6 text-sm text-slate-400">Loading…</p>
        )}
        {ordered?.length === 0 && (
          <div className="px-4 py-6 text-sm text-slate-500">
            <p>Nothing here yet.</p>
            <p className="mt-1 text-slate-400">
              Import a GenBank file above, or run{" "}
              <code className="rounded bg-slate-100 px-1 py-0.5 text-[11px]">
                uv run python -m app.seed
              </code>{" "}
              in <code className="text-[11px]">backend/</code> for the three
              scenarios above.
            </p>
          </div>
        )}
        <ul className="divide-y divide-slate-100">
          {ordered?.map((construct) => (
            <li
              key={construct.id}
              className={`group flex items-start hover:bg-slate-50 ${
                construct.parent_id ? "border-l-2 border-l-slate-200 pl-2" : ""
              }`}
            >
              <Link
                href={`/constructs/${construct.id}`}
                className="min-w-0 flex-1 px-4 py-2.5"
              >
                <span className="flex items-baseline gap-3">
                  <span className="flex-1 truncate text-sm font-medium text-slate-800">
                    {construct.parent_id && (
                      <span className="mr-1 text-slate-300">⑂</span>
                    )}
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
                </span>
                {construct.description && (
                  <span className="mt-0.5 block max-w-2xl text-xs leading-snug text-slate-500">
                    {construct.description}
                  </span>
                )}
              </Link>
              <button
                type="button"
                disabled={busy}
                onClick={() => void remove(construct)}
                title={`Delete ${construct.name}`}
                aria-label={`Delete ${construct.name}`}
                className="mr-2 mt-2 shrink-0 rounded px-2 py-1 text-sm text-slate-300 opacity-0 hover:bg-red-50 hover:text-red-600 focus:opacity-100 disabled:opacity-20 group-hover:opacity-100"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
