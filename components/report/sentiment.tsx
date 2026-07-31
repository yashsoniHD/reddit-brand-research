import { StackedBar, type StackedSegment } from "@/components/charts/stacked-bar";
import { Note, Section } from "@/components/report/section";
import {
  SENTIMENT_LABELS,
  SENTIMENT_ORDER,
  type Bundle,
  type SentimentKey,
} from "@/lib/types";

const COLOURS: Record<string, string> = {
  pos: "var(--viz-pos)",
  neu: "var(--viz-neu)",
  neg: "var(--viz-neg)",
};

const EMPTY_COUNTS = { pos: 0, neu: 0, neg: 0 };

/**
 * Left to right along the polarity axis.
 *
 * SENTIMENT_ORDER is the engine's reading order and stays that way everywhere
 * else. The bar's legend reads in axis order instead so that the swatch list
 * and the bar cannot disagree about which side of neutral a colour lives on.
 */
const AXIS_ORDER: SentimentKey[] = ["neg", "neu", "pos"];

export function Sentiment({ bundle }: { bundle: Bundle }) {
  const stats = bundle.stats?.sentiment;
  const counts = stats?.counts ?? EMPTY_COUNTS;
  const percentages = stats?.percentages ?? EMPTY_COUNTS;
  const sample = bundle.sample;

  const total = SENTIMENT_ORDER.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  // A run can finish with nothing classified: the tone pass may have failed, or
  // every item may have come back unusable. Three rows of 0% look like a finding
  // ("this conversation is perfectly neutral") when they are the absence of one.
  // Either signal on its own is enough to suppress the chart, so this is `||`:
  // with `&&`, a bundle reporting classified: 50 alongside all-zero counts drew
  // the three empty rows this is here to prevent.
  const nothingClassified = (stats?.classified ?? total) === 0 || total === 0;

  const segments: StackedSegment[] = AXIS_ORDER.map((key) => ({
    key,
    label: SENTIMENT_LABELS[key],
    percent: percentages[key] ?? 0,
    count: counts[key] ?? 0,
    colour: COLOURS[key],
    pole: key,
  }));

  const showBar = !nothingClassified && !sample?.low_data;

  return (
    <Section title="Sentiment">
      {nothingClassified ? (
        <p className="text-sm text-muted-foreground">
          No items could be classified for tone in this run.
        </p>
      ) : sample?.low_data ? (
        // Percentages of nine items are theatre, so a thin sample gets counts.
        <ul className="flex list-none flex-wrap gap-x-5 gap-y-2 p-0 text-sm">
          {SENTIMENT_ORDER.map((key) => (
            <li key={key} className="flex items-baseline gap-2">
              <span
                aria-hidden="true"
                className="size-2.5 shrink-0 translate-y-px rounded-full"
                style={{ background: COLOURS[key] }}
              />
              <span className="text-foreground-soft">{SENTIMENT_LABELS[key]}</span>
              <span className="num font-semibold text-foreground">
                {counts[key] ?? 0}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <StackedBar segments={segments} />
      )}

      {nothingClassified ? null : (
        <Note>
          {/* The centred axis is the one thing about this bar a reader cannot
              work out from the numbers alone, so it is said in words. */}
          {showBar
            ? "The bar is centred on neutral: negative reads to the left of the line, positive to the right. "
            : null}
          Classified per item across {sample?.posts_analysed ?? 0} posts and{" "}
          {sample?.comments_analysed ?? 0} comments. Percentages are computed from those
          classifications, not estimated.
        </Note>
      )}
    </Section>
  );
}
