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
  // The track means "the whole sample" in both modes, so both denominators are
  // the sample. Shares are already that. Counts are scaled against
  // items_analysed rather than against the largest topic, because scaling to
  // the leader makes the top bar full width by construction: a filled track
  // next to "12 items" reads as "this is everything", which is the overclaim
  // this scale exists to avoid.
  const analysed = Math.max(1, Number(bundle.sample?.items_analysed) || 0);
  const widthFor = (share: number, count: number) =>
    lowData
      ? Math.min(100, (count / analysed) * 100)
      : Math.min(100, Math.max(0, share));

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
      <ul data-viz className="flex list-none flex-col gap-0 p-0">
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
                <span className="num t-body shrink-0 font-semibold text-foreground">
                  {valueLabel}
                </span>
              </div>

              {/* One series, so no legend: the section title names the measure,
                  and every bar is directly labelled above it, which is also why
                  the bar carries no accessible text of its own. */}
              <div
                aria-hidden="true"
                className="mt-2 h-1.5 w-full overflow-hidden rounded-r-full"
                style={{ background: "var(--viz-track)" }}
              >
                {/* Rounded at the data end, square at the baseline, so the bar
                    reads as growing from the left rather than floating. The
                    track is rounded on its right edge only: rounded-full rounded
                    the left edge too, and overflow-hidden then clipped the
                    fill's square origin straight back into a semicircle. */}
                <div
                  className="h-full rounded-r-[3px]"
                  style={{
                    width: `${width}%`,
                    // A percentage floor overstated a 0.3% topic by five times.
                    // The floor is in pixels instead, so a sliver reads as
                    // too small to draw rather than as a measurable length.
                    minWidth: width > 0 ? 2 : 0,
                    background: "var(--viz-accent)",
                  }}
                />
              </div>

              {topic.finding ? (
                <p className="t-small mt-2.5 max-w-[78ch] text-muted-foreground">
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
