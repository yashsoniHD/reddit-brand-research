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
        <div className="grid gap-3 sm:grid-cols-3">
          {SENTIMENT_ORDER.map((key) => (
            <div key={key} className="rounded-xl border bg-card p-4">
              <p className="flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                <span
                  aria-hidden="true"
                  className="size-2.5 rounded-full"
                  style={{ background: COLOURS[key] }}
                />
                {SENTIMENT_LABELS[key]}
              </p>
              <p className="mt-1.5 text-xl font-semibold text-foreground">
                {counts[key] ?? 0}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <StackedBar segments={segments} />
      )}

      <Note>
        Classified per item by the model across {sample.posts_analysed} posts and{" "}
        {sample.comments_analysed} comments. Percentages are computed from those
        classifications, not estimated.
      </Note>
    </Section>
  );
}
