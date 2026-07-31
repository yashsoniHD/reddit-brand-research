import { shareText, thousands } from "@/lib/format";

/**
 * One diverging bar for an ordered three way split.
 *
 * Polarity is not three unrelated categories. Left baselined, the reader is
 * asked to compare three segment lengths and infer a direction from them; the
 * question they actually have is which side of neutral the conversation sits
 * on. So the encoding is centred: the negative pole grows leftward from the
 * midpoint, the positive pole rightward, and the neutral band straddles it.
 *
 * The axis domain is fixed at -100 to +100 so zero is always the geometric
 * centre of the track. The price of a stable zero is that the drawn bar is
 * exactly half the track wide at every mix: it slides rather than stretches,
 * and its position is the finding. A domain scaled to the data would make 20%
 * negative look identical to 100% negative.
 *
 * The legend below always names and numbers each segment, so identity never
 * depends on colour alone.
 */

/** Where a segment sits on the polarity axis. */
export type Pole = "neg" | "neu" | "pos";

export interface StackedSegment {
  key: string;
  label: string;
  /** Percent of the classified sample. Not assumed to be pre-rounded. */
  percent: number;
  count: number;
  colour: string;
  /**
   * Drives the geometry. The array order drives only the legend, so a caller
   * can list the segments in whatever order reads best without moving them on
   * the axis.
   */
  pole: Pole;
}

/** Left to right along the axis. */
const POLE_ORDER: Record<Pole, number> = { neg: 0, neu: 1, pos: 2 };

/** Height of the bar itself, px. */
const BAR_HEIGHT = 28;
/** How far the zero line shows above and below the bar, px. */
const TICK_OVERHANG = 5;
/** Surface coloured separator between two adjacent segments, px. */
const SEPARATOR = 2;
/** One point of share is half a percent of the track, because the domain
 *  spans 200 points rather than 100. */
const UNIT = 0.5;
/** A sliver still has to be visible as a mark, px. */
const MIN_SEGMENT = 2;

export function StackedBar({ segments }: { segments: StackedSegment[] }) {
  // Percentages arrive from the engine unrounded and are not guaranteed to add
  // to exactly 100, so the geometry is normalised against their own total while
  // the labels keep printing the reported values. The container used to crop
  // the excess with overflow-hidden, which absorbed the discrepancy by silently
  // shortening whichever segment happened to be last.
  const reported = segments.map((segment) => Math.max(0, Number(segment.percent) || 0));
  const total = reported.reduce((sum, value) => sum + value, 0);
  const scale = total > 0 ? 100 / total : 0;

  const laid = segments
    .map((segment, index) => ({ segment, share: reported[index] * scale }))
    .sort((a, b) => POLE_ORDER[a.segment.pole] - POLE_ORDER[b.segment.pole]);

  const shareOf = (pole: Pole) =>
    laid
      .filter((entry) => entry.segment.pole === pole)
      .reduce((sum, entry) => sum + entry.share, 0);

  // Half the neutral band sits either side of the midpoint, so the zero line
  // falls exactly where the conversation has stopped being negative and has not
  // yet turned positive.
  const barLeft = 50 - (shareOf("neg") + shareOf("neu") / 2) * UNIT;
  const drawn = laid.filter((entry) => entry.share > 0);
  const placed = drawn.map((entry, index) => ({
    ...entry,
    // Three segments at most, so summing the run in front of each one is
    // cheaper than carrying a mutable cursor through the render.
    left: barLeft + drawn.slice(0, index).reduce((sum, run) => sum + run.share, 0) * UNIT,
    width: entry.share * UNIT,
  }));

  return (
    <figure className="m-0">
      {/* Every value in this bar is printed as text in the legend below, so the
          bar itself is hidden from the accessibility tree: naming it as well
          would read every number twice. */}
      <div
        data-viz
        aria-hidden="true"
        className="relative"
        style={{ paddingBlock: TICK_OVERHANG }}
      >
        {/* The polarity axis. It sits behind the bar rather than over it:
            --viz-grid is a hairline tuned against the surface and would vanish
            on --viz-neu, which is the one segment guaranteed to cover the
            centre. What stays visible is the tick overhanging top and bottom. */}
        <div
          className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2"
          style={{ background: "var(--viz-grid)" }}
        />

        <div
          className="relative w-full rounded-lg"
          style={{ height: BAR_HEIGHT, background: "var(--background)" }}
        >
          {placed.map((entry, index) => (
            <span
              key={entry.segment.key}
              className={[
                "absolute inset-y-0 block",
                index === 0 ? "rounded-l-lg" : "",
                index === placed.length - 1 ? "rounded-r-lg" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              style={{
                left: `${entry.left}%`,
                width: `${entry.width}%`,
                minWidth: MIN_SEGMENT,
                background: entry.segment.colour,
              }}
            />
          ))}

          {/* The separator is painted in the surface colour rather than the
              track colour. On the track, light --viz-neu measured 2.70:1 and
              dark --viz-neg 2.98:1, both under the 3:1 floor for a mark; against
              the real surfaces both pass. */}
          {placed.slice(1).map((entry) => (
            <span
              key={`gap-${entry.segment.key}`}
              className="absolute inset-y-0 block"
              style={{
                left: `calc(${entry.left}% - ${SEPARATOR / 2}px)`,
                width: SEPARATOR,
                background: "var(--background)",
              }}
            />
          ))}
        </div>
      </div>

      {/* Every segment is named and numbered here, so nothing in the bar above
          depends on the reader telling teal from grey. */}
      <ul className="mt-3 flex list-none flex-wrap gap-x-5 gap-y-2 p-0 text-sm">
        {segments.map((segment) => (
          <li key={segment.key} className="flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 translate-y-px rounded-full"
              style={{ background: segment.colour }}
            />
            <span className="text-foreground-soft">{segment.label}</span>
            <span className="num font-semibold text-foreground">
              {shareText(segment.percent)}%
            </span>
            <span className="num text-xs text-muted-foreground">
              ({thousands(segment.count)})
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
