"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { BranchSummary, ConstructDetail, MergePreview } from "@/lib/types";

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

  const doMerge = (branchId: string, force = false) =>
    guard(async () => {
      setRefused(null);
      await api.mergeBranch(construct.id, branchId, force);
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

          {refused && <RefusalReport preview={refused} onForce={doMerge} />}
        </div>
      )}
    </div>
  );
}

function RefusalReport({
  preview,
  onForce,
}: {
  preview: MergePreview;
  onForce: (branchId: string, force: boolean) => void;
}) {
  const blocking = preview.new_frame_issues.filter((i) => i.blocking);

  return (
    <div className="mt-3 rounded border border-red-200 bg-red-50 p-2">
      {preview.conflicts.length > 0 ? (
        <>
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
            Coordinate clashes cannot be forced — undo one side, or redo the
            edit against the merged sequence.
          </p>
        </>
      ) : (
        <>
          <p className="font-semibold text-red-900">
            The merge applies cleanly but breaks a reading frame.
          </p>
          <ul className="mt-1 list-disc pl-4 text-red-800">
            {blocking.map((issue) => (
              <li key={`${issue.feature_id}-${issue.problem}`}>
                <span className="font-medium">{issue.feature_name}</span>:{" "}
                {issue.detail}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[11px] text-red-700">
            Neither branch had this problem on its own — the combination
            created it.
          </p>
          <button
            type="button"
            onClick={() => onForce(preview.branch_id, true)}
            className="mt-2 rounded border border-red-300 bg-white px-2 py-0.5 text-red-800 hover:bg-red-100"
          >
            Merge anyway
          </button>
        </>
      )}
    </div>
  );
}
