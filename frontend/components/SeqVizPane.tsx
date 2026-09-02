"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

import { featureColor, translucent } from "@/lib/colors";
import type {
  ConstructDetail,
  Feature,
  FrameIssue,
  SelectionRange,
} from "@/lib/types";

// seqviz measures the DOM on mount, so it can only render in the browser.
const SeqViz = dynamic(() => import("seqviz").then((m) => m.SeqViz), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-xs text-slate-400">
      Loading viewer…
    </div>
  ),
});

interface Props {
  construct: ConstructDetail;
  enzymes: string[];
  /**
   * A range to scroll to. SeqViz moves the linear viewer whenever `selection`
   * arrives as a prop rather than from a drag, so this is set briefly and then
   * cleared - leaving it set would override the user's own selections.
   */
  focus?: SelectionRange | null;
  onSelection: (
    selection: SelectionRange | null,
    annotationName: string | null,
  ) => void;
}

/** The blocking issue that truncates this feature, if any. */
function brokenBy(
  issues: FrameIssue[],
  feature: Feature,
): FrameIssue | undefined {
  return issues.find(
    (i) =>
      i.blocking &&
      i.feature_id === feature.id &&
      i.translated_start !== null &&
      i.translated_end !== null &&
      i.stop_start !== null &&
      i.stop_end !== null,
  );
}

/**
 * The stretch downstream of the stop: still annotated, never made into
 * protein. It is the low side of the feature for a gene read on the minus
 * strand, and the high side for one read on the plus strand.
 */
function deadSpan(feature: Feature, issue: FrameIssue) {
  return feature.strand === -1
    ? { start: feature.start, end: issue.stop_start as number }
    : { start: issue.stop_end as number, end: feature.end };
}

export function SeqVizPane({ construct, enzymes, focus, onSelection }: Props) {
  // seqviz renders `start > end` annotations across the origin natively, so
  // origin-crossing features can be passed straight through. The exception is
  // a gene a premature stop has cut in half: that one is split so the part
  // that never gets translated reads as inert.
  const annotations = useMemo(
    () =>
      construct.features.flatMap((feature) => {
        const colour = feature.color ?? featureColor(feature.kind, feature.name);
        const broken = brokenBy(construct.frame_issues, feature);
        if (!broken) {
          return [
            {
              name: feature.name,
              start: feature.start,
              end: feature.end,
              direction: feature.strand,
              color: colour,
            },
          ];
        }
        return [
          {
            name: feature.name,
            start: broken.translated_start!,
            end: broken.translated_end!,
            direction: feature.strand,
            color: colour,
          },
          {
            name: `${feature.name} (not translated)`,
            ...deadSpan(feature, broken),
            direction: feature.strand,
            color: translucent(colour, 0.25),
          },
        ];
      }),
    [construct.features, construct.frame_issues],
  );

  // Mark the codon that ends translation, so the break is findable on the map.
  const highlights = useMemo(
    () =>
      construct.frame_issues
        .filter((i) => i.blocking && i.stop_start !== null)
        .map((i) => ({
          start: i.stop_start as number,
          end: i.stop_end as number,
          color: "rgba(220, 38, 38, 0.35)",
        })),
    [construct.frame_issues],
  );

  const enzymeNames = useMemo(
    // seqviz looks its built-in enzyme database up by lowercased name.
    () => enzymes.map((name) => name.toLowerCase()),
    [enzymes],
  );

  return (
    <div className="h-full w-full">
      <SeqViz
        key={construct.id}
        name={construct.name}
        seq={construct.sequence}
        seqType="dna"
        viewer="both"
        annotations={annotations}
        highlights={highlights}
        selection={
          focus ? { start: focus.start, end: focus.end, clockwise: true } : undefined
        }
        enzymes={enzymeNames}
        primers={[]}
        showComplement
        showIndex
        style={{ height: "100%", width: "100%" }}
        onSelection={(selection) => {
          const { start, end, type } = selection;
          if (
            typeof start !== "number" ||
            typeof end !== "number" ||
            start === end
          ) {
            // A bare click clears the range but still moves the cursor.
            onSelection(
              typeof start === "number" ? { start, end: start } : null,
              null,
            );
            return;
          }
          onSelection(
            { start, end },
            type === "ANNOTATION" ? (selection.name ?? null) : null,
          );
        }}
      />
    </div>
  );
}
