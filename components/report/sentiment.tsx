import { StackedBar, type StackedSegment } from "@/components/charts/stacked-bar";
import { Note, Section } from "@/components/report/section";
import { SENTIMENT_LABELS, SENTIMENT_ORDER, type Bundle } from "@/lib/types";

const COLOURS: Record<string, string> = {
  pos: "var(--viz-pos)",
  neu: "var(--viz-neu)",
  neg: "var(--viz-neg)",
};

const EMPTY_COUNTS = { pos: 0, neu: 0, neg: 0 };

export function Sentiment({ bundle }: { bundle: Bundle }) {
  const stats = bundle.stats?.sentiment;
  const counts = stats?.counts ?? EMPTY_COUNTS;
  const percentages = stats?.percentages ?? EMPTY_COUNTS;
  const sample = bundle.sample;

  const total = SENTIMENT_ORDER.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  // A run can finish with nothing classified: the tone pass may have failed, or
  // every item may have come back unusable. Three rows of 0% look like a finding
  // ("this conversation is perfectly neutral") when they are the absence of one.
  const nothingClassified = (stats?.classified ?? total) === 0 && total === 0;

  const segments: StackedSegment[] = SENTIMENT_ORDER.map((key) => ({
    key,
    label: SENTIMENT_LABELS[key],
    percent: percentages[key] ?? 0,
    count: counts[key] ?? 0,
    colour: COLOURS[key],
  }));

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
          Classified per item across {sample?.posts_analysed ?? 0} posts and{" "}
          {sample?.comments_analysed ?? 0} comments. Percentages are computed from those
          classifications, not estimated.
        </Note>
      )}
    </Section>
  );
}
