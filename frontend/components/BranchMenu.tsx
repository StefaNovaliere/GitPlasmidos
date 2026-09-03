"use client";

import { useCallback, useEffect, useState } from "react";

import { DiffView } from "@/components/DiffView";
import { MergeConflictView } from "@/components/MergeConflictView";
import { MergeRuleBlock } from "@/components/MergeRuleBlock";
import { ApiError, api } from "@/lib/api";
import type {
  BranchSummary,
  ConstructDetail,
  MergePreview,
  MergeSuppression,
} from "@/lib/types";

interface Props {
  construct: ConstructDetail;
  busy: boolean;
  onMerged: () => void;
  onBranchCreated: (id: string) => void;
  onError: (message: string) => void;
}

function isMergePreview(value: unknown): value is MergePreview {
  return (
    typeof value === "object" &&
    value !== null &&
    "conflicts" in value &&
    "new_frame_issues" in value
  );
}

export function BranchMenu({
  construct,
  busy,
  onMerged,
  onBranchCreated,
  onError,
}: Props) {
  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState<BranchSummary[]>([]);
  const [refused, setRefused] = useState<MergePreview | null>(null);
  const [comparing, setComparing] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    try {
      setBranches(await api.listBranches(construct.id));
    } catch {
      setBranches([]);
    }
  }, [construct.id]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload, construct.updated_at]);

  const guard = async (action: () => Promise<void>) => {
    setWorking(true);
    try {
      await action();
    } catch (err) {
      if (err instanceof ApiError && isMergePreview(err.detail)) {
        setRefused(err.detail);
      } else {
        onError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setWorking(false);
    }
  };

  const fork = () =>
    guard(async () => {
      const created = await api.createBranch(construct.id, "");
      onBranchCreated(created.id);
    });

  const doMerge = (
    branchId: string,
    force = false,
    suppress: MergeSuppression[] = [],
  ) =>
    guard(async () => {
      setRefused(null);
      await api.mergeBranch(construct.id, branchId, force, suppress);
      await reload();
      onMerged();
    });

  const disabled = busy || working;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-100"
        title="Branch this construct, or merge a branch back in"
      >
        ⑂ Branches{branches.length > 0 ? ` (${branches.length})` : ""}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-40 mt-1 w-[26rem] rounded-md border border-slate-200 bg-white p-3 text-xs shadow-lg">
          <div className="flex items-center justify-between">
            <span className="font-semibold uppercase tracking-wide text-slate-500">
              Branches
            </span>
            <button
              type="button"
              disabled={disabled}
              onClick={fork}
              className="rounded bg-slate-800 px-2 py-1 font-medium text-white hover:bg-slate-700 disabled:opacity-40"
            >
              New branch
            </button>
          </div>

          {construct.parent_id && (
            <p className="mt-2 rounded bg-slate-50 px-2 py-1 text-[11px] text-slate-500">
              This construct is itself a branch. Merge it from its parent.
            </p>
          )}

          {branches.length === 0 ? (
            <p className="mt-3 text-slate-400">
              No branches yet. Forking copies the sequence and the history, so
              both sides can be edited independently and reconciled later.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-slate-100">
              {branches.map((branch) => (
                <li
                  key={branch.id}
                  className="flex items-center gap-2 py-1.5"
                >
                  <span className="min-w-0 flex-1">
                    <a
                      href={`/constructs/${branch.id}`}
                      className="block truncate font-medium text-slate-800 hover:underline"
                    >
                      {branch.name}
                    </a>
                    <span className="font-mono text-[10px] text-slate-400">
                      {branch.length.toLocaleString()} bp · {branch.ahead} edit
                      {branch.ahead === 1 ? "" : "s"} ahead
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => setComparing(branch.id)}
                    className="rounded border border-slate-300 px-2 py-0.5 text-slate-700 hover:bg-slate-100"
                  >
                    Compare
                  </button>
                  <button
                    type="button"
                    disabled={disabled || branch.ahead === 0}
                    onClick={() => void doMerge(branch.id)}
                    className="rounded border border-slate-300 px-2 py-0.5 text-slate-700 hover:bg-slate-100 disabled:opacity-40"
                  >
                    Merge
                  </button>
                </li>
              ))}
            </ul>
          )}

          {refused && refused.conflicts.length > 0 && (
            <ConflictReport preview={refused} />
          )}
        </div>
      )}

      {/* Rule errors first: that gate needs a decision, the frame gate only
          needs a click, and a merge has to clear both. */}
      {refused && refused.conflicts.length === 0 && refused.new_findings.length > 0 && (
        <MergeRuleBlock
          preview={refused}
          onRecord={(decisions) =>
            void doMerge(refused.branch_id, false, decisions)
          }
          onClose={() => setRefused(null)}
        />
      )}

      {refused && refused.conflicts.length === 0 && refused.new_findings.length === 0 && (
        <MergeConflictView
          preview={refused}
          target={construct}
          onForce={() => void doMerge(refused.branch_id, true)}
          onClose={() => setRefused(null)}
        />
      )}

      {comparing && (
        <DiffView
          constructId={construct.id}
          againstId={comparing}
          onClose={() => setComparing(null)}
        />
      )}
    </div>
  );
}

function ConflictReport({ preview }: { preview: MergePreview }) {
  return (
    <div className="mt-3 rounded border border-red-200 bg-red-50 p-2">
      <p className="font-semibold text-red-900">
        The two branches edited the same bases.
      </p>
      <ul className="mt-1 list-disc pl-4 text-red-800">
        {preview.conflicts.map((c) => (
          <li key={`${c.branch_index}-${c.reason}`}>
            operation {c.branch_index} ({c.kind}): {c.detail}
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-[11px] text-red-700">
        Coordinate clashes cannot be forced — undo one side, or redo the edit
        against the merged sequence.
      </p>
    </div>
  );
}
