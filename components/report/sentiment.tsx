import { StackedBar, type StackedSegment } from "@/components/charts/stacked-bar";
import { Note, Section } from "@/components/report/section";
import { SENTIMENT_LABELS, SENTIMENT_ORDER, type Bundle } from "@/lib/types";

const COLOURS: Record<string, string> = {
  pos: "var(--viz-pos)",
  neu: "var(--viz-neu)",
  neg: "var(--viz-neg)",
};

export function Sentiment({ bundle }: { bundle: Bundle }) {
  const { counts, percentages } = bundle.stats.sentiment;
  const sample = bundle.sample;

  const segments: StackedSegment[] = SENTIMENT_ORDER.map((key) => ({
    key,
    label: SENTIMENT_LABELS[key],
    percent: percentages[key] ?? 0,
    count: counts[key] ?? 0,
    colour: COLOURS[key],
  }));

  return (
    <Section title="Sentiment">
      {sample.low_data ? (
        // Percentages of nine items are theatre, so a thin sample gets counts.
        <ul className="flex list-none flex-wrap gap-x-6 gap-y-2 p-0 text-sm">
          {SENTIMENT_ORDER.map((key) => (
            <li key={key} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="size-2.5 rounded-full"
                style={{ background: COLOURS[key] }}
              />
              <span className="text-foreground">{SENTIMENT_LABELS[key]}</span>
              <span className="tabular-nums text-muted-foreground">
                {counts[key] ?? 0}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <StackedBar segments={segments} />
      )}

      <Note>
        Classified per item across {sample.posts_analysed} posts and{" "}
        {sample.comments_analysed} comments. Percentages are computed from those
        classifications, not estimated.
      </Note>
    </Section>
  );
}
