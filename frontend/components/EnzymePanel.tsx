"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { EnzymeSite } from "@/lib/types";

interface Props {
  constructId: string;
  /** Changes whenever the derived sequence changes, forcing a re-digest. */
  revision: string;
  selected: string[];
  onChange: (names: string[]) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export function EnzymePanel({
  constructId,
  revision,
  selected,
  onChange,
  collapsed,
  onToggleCollapsed,
}: Props) {
  const [sites, setSites] = useState<EnzymeSite[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .enzymes(constructId, showAll)
      .then((body) => {
        if (live) setSites(body.enzymes);
      })
      .catch(() => {
        if (live) setSites([]);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [constructId, showAll, revision]);

  // Drop selections for enzymes that stopped cutting after an edit.
  useEffect(() => {
    if (loading) return;
    const available = new Set(sites.map((s) => s.name));
    const stillThere = selected.filter((name) => available.has(name));
    if (stillThere.length !== selected.length) onChange(stillThere);
  }, [sites, loading, selected, onChange]);

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onToggleCollapsed}
        title="Show restriction enzymes"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-l border-slate-200 bg-white py-3 text-xs text-slate-500 hover:bg-slate-50"
      >
        <span aria-hidden>‹</span>
        <span className="[writing-mode:vertical-rl] tracking-wide">Enzymes</span>
        {selected.length > 0 && (
          <span className="rounded bg-slate-800 px-1 text-[10px] text-white">
            {selected.length}
          </span>
        )}
      </button>
    );
  }

  const toggle = (name: string) => {
    onChange(
      selected.includes(name)
        ? selected.filter((n) => n !== name)
        : [...selected, name],
    );
  };

  return (
    <aside className="flex w-64 shrink-0 flex-col border-l border-slate-200 bg-white">
      <header className="flex items-center justify-between px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Enzymes
        </h2>
        <button
          type="button"
          onClick={onToggleCollapsed}
          title="Hide panel"
          className="rounded px-1 text-slate-400 hover:bg-slate-100"
        >
          ›
        </button>
      </header>

      <div className="flex items-center justify-between gap-2 px-3 pb-2 text-[11px] text-slate-500">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          Include multi-cutters
        </label>
        {selected.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="text-slate-400 underline hover:text-slate-700"
          >
            clear
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-3">
        {loading && (
          <p className="px-2 py-4 text-xs text-slate-400">Digesting…</p>
        )}
        {!loading && sites.length === 0 && (
          <p className="px-2 py-4 text-xs text-slate-400">
            No cut sites found.
          </p>
        )}
        {sites.map((site) => (
          <label
            key={site.name}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-slate-50"
            title={`${site.site} · ${site.overhang} overhang`}
          >
            <input
              type="checkbox"
              checked={selected.includes(site.name)}
              onChange={() => toggle(site.name)}
            />
            <span className="flex-1 font-medium text-slate-800">
              {site.name}
            </span>
            <span className="font-mono text-[10px] text-slate-400">
              {site.cuts === 1
                ? site.cut_positions[0] + 1
                : `${site.cuts}×`}
            </span>
          </label>
        ))}
      </div>

      <p className="border-t border-slate-100 px-3 py-2 text-[10px] leading-snug text-slate-400">
        Single cutters from a curated cloning set. Positions are 1-based and
        follow the current derived sequence.
      </p>
    </aside>
  );
}
