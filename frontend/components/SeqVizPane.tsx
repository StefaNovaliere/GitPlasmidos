"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

import { featureColor } from "@/lib/colors";
import type { ConstructDetail, SelectionRange } from "@/lib/types";

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
  onSelection: (
    selection: SelectionRange | null,
    annotationName: string | null,
  ) => void;
}

export function SeqVizPane({ construct, enzymes, onSelection }: Props) {
  // seqviz renders `start > end` annotations across the origin natively, so
  // origin-crossing features can be passed straight through.
  const annotations = useMemo(
    () =>
      construct.features.map((feature) => ({
        name: feature.name,
        start: feature.start,
        end: feature.end,
        direction: feature.strand,
        color: feature.color ?? featureColor(feature.kind, feature.name),
      })),
    [construct.features],
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
