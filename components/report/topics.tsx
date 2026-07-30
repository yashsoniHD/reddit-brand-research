import { EvidencePills } from "@/components/report/evidence-pills";
import { Note, Section } from "@/components/report/section";
import { shareText } from "@/lib/format";
import type { Bundle } from "@/lib/types";

/** Below this the shares clearly do not account for the whole sample. */
const FULL_COVERAGE = 99;

/**
 * Topic volume and the finding behind each one, in one block.
 *
 * The bar, the label, the value and the finding now live in a single row rather
 * than in a chart followed by a duplicate list of expanders, which halves the
 * vertical space and removes the repetition.
 */
export function Topics({ bundle }: { bundle: Bundle }) {
  const topics = bundle.analysis.topics ?? [];
  const lowData = bundle.sample.low_data;

  if (topics.length === 0) {
    return (
      <Section title="Trending topics">
        <p className="text-xs text-muted-foreground">
          No recurring topics were identified in this sample.
        </p>
      </Section>
    );
  }

  // In a thin sample a percentage implies a precision the data does not have,
  // so the measure switches to plain item counts.
  const measure = (share: number, count: number) => (lowData ? count : share);
  const ordered = [...topics].sort(
    (a, b) => measure(b.share, b.count) - measure(a.share, a.count),
  );
  // Counts have no natural ceiling, so those bars stay relative to the biggest
  // one. Shares do: the bar for 17% is 17% of the track, because a bar scaled to
  // the largest topic silently turns "17% of the conversation" into "the leader",
  // which is a different and much bolder claim than the data supports.
  const countMax = Math.max(...ordered.map((topic) => topic.count), 1);
  const widthFor = (share: number, count: number) =>
    lowData
      ? Math.max(1.5, (count / countMax) * 100)
      : Math.min(100, Math.max(1.5, share));

  const totalShare = ordered.reduce((sum, topic) => sum + (Number(topic.share) || 0), 0);
  const partialCoverage = !lowData && totalShare < FULL_COVERAGE;

  return (
    <Section
      title="Trending topics"
      description={
        lowData
          ? "Items mentioning each topic, from per item classifications."
          : "Share of the analysed conversation, from per item classifications."
      }
    >
      <ul className="flex list-none flex-col gap-0 p-0">
        {ordered.map((topic) => {
          const width = widthFor(topic.share, topic.count);
          const valueLabel = lowData
            ? `${topic.count} ${topic.count === 1 ? "item" : "items"}`
            : `${shareText(topic.share)}%`;

          return (
            <li key={String(topic.id)} className="border-b py-3.5 last:border-b-0">
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm leading-snug font-semibold text-foreground">
                  {topic.label}
                </span>
                {/* The measure is the point of the row, so it is sized like a
                    number rather than like a caption. */}
                <span className="num shrink-0 text-[0.9375rem] font-semibold text-foreground">
                  {valueLabel}
                </span>
              </div>

              {/* One series, so no legend: the section title names the measure,
                  and every bar is directly labelled above it. */}
              <div
                className="mt-2 h-1.5 w-full overflow-hidden rounded-full"
                style={{ background: "var(--viz-track)" }}
                role="img"
                aria-label={`${topic.label}: ${valueLabel}`}
              >
                {/* Rounded at the data end, square at the baseline, so the bar
                    reads as growing from the left rather than floating. The
                    track's own rounding clips the left edge, so nothing looks
                    cut off. */}
                <div
                  className="h-full rounded-r-[3px]"
                  style={{ width: `${width}%`, background: "var(--viz-accent)" }}
                />
              </div>

              {topic.finding ? (
                <p className="mt-2.5 max-w-[78ch] text-[0.8125rem] leading-relaxed text-muted-foreground">
                  {topic.finding}{" "}
                  <EvidencePills citations={topic.citations ?? []} label="Source" />
                </p>
              ) : (
                <EvidencePills
                  citations={topic.citations ?? []}
                  label="Source"
                  className="mt-2.5"
                />
              )}
            </li>
          );
        })}
      </ul>

      {partialCoverage ? (
        <Note>
          Bars are drawn to the full width of the section, so a topic at 20% fills a fifth
          of it. These shares add up to {shareText(totalShare)}%: smaller topics are not
          shown.
        </Note>
      ) : null}
    </Section>
  );
}
