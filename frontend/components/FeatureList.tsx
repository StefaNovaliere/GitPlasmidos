"use client";

import { featureColor } from "@/lib/colors";
import { crossesOrigin, formatRange, spanLength } from "@/lib/sequence";
import type { Feature, FrameIssue } from "@/lib/types";

interface Props {
  features: Feature[];
  length: number;
  isCircular: boolean;
  frameIssues: FrameIssue[];
  selectedId: string | null;
  onSelect: (feature: Feature) => void;
  onRemove: (feature: Feature) => void;
}

export function FeatureList({
  features,
  length,
  isCircular,
  frameIssues,
  selectedId,
  onSelect,
  onRemove,
}: Props) {
  const sorted = [...features].sort((a, b) => a.start - b.start);
  const issuesByFeature = new Map<string, FrameIssue[]>();
  for (const issue of frameIssues) {
    issuesByFeature.set(issue.feature_id, [
      ...(issuesByFeature.get(issue.feature_id) ?? []),
      issue,
    ]);
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Features
        </h2>
        <span className="text-xs text-slate-400">{features.length}</span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {sorted.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-slate-400">
            No features yet. Select a range and use “Annotate as…”.
          </p>
        )}
        <ul>
          {sorted.map((feature) => {
            const selected = feature.id === selectedId;
            const issues = issuesByFeature.get(feature.id) ?? [];
            const broken = issues.some((i) => i.blocking);
            return (
              <li key={feature.id}>
                <div
                  className={`group flex w-full items-start gap-2 border-l-2 px-3 py-1.5 text-left ${
                    selected
                      ? "border-l-slate-800 bg-slate-100"
                      : "border-l-transparent hover:bg-slate-50"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(feature)}
                    className="flex min-w-0 flex-1 items-start gap-2"
                    title={`${feature.name} · ${feature.kind}`}
                  >
                    <span
                      aria-hidden
                      className="mt-1 h-2.5 w-2.5 shrink-0 rounded-sm"
                      style={{
                        background:
                          feature.color ??
                          featureColor(feature.kind, feature.name),
                      }}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-xs font-medium text-slate-800">
                          {feature.name}
                        </span>
                        {issues.length > 0 && (
                          <span
                            title={issues.map((i) => i.detail).join("\n")}
                            className={`shrink-0 rounded px-1 text-[10px] font-semibold ${
                              broken
                                ? "bg-red-100 text-red-800"
                                : "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {broken ? issues[0].problem.replace("_", " ") : "frame"}
                          </span>
                        )}
                        {feature.truncated && (
                          <span
                            title="Clipped by an edit"
                            className="shrink-0 rounded bg-amber-100 px-1 text-[10px] font-semibold text-amber-800"
                          >
                            trunc
                          </span>
                        )}
                        {crossesOrigin(feature) && (
                          <span
                            title="Crosses the origin"
                            className="shrink-0 rounded bg-sky-100 px-1 text-[10px] font-semibold text-sky-800"
                          >
                            ↻
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                        <span className="truncate">{feature.kind}</span>
                        <span className="font-mono">
                          {formatRange(feature.start, feature.end)}
                        </span>
                        <span aria-label={feature.strand === 1 ? "forward" : "reverse"}>
                          {feature.strand === 1 ? "→" : "←"}
                        </span>
                        <span className="text-slate-400">
                          {spanLength(
                            feature.start,
                            feature.end,
                            length,
                            isCircular,
                          )}{" "}
                          bp
                        </span>
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemove(feature)}
                    title={`Remove ${feature.name}`}
                    className="mt-0.5 shrink-0 rounded px-1 text-xs text-slate-300 opacity-0 hover:bg-red-50 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"
                  >
                    ×
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
