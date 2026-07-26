import { EvidencePills } from "@/components/report/evidence-pills";
import { Section } from "@/components/report/section";
import { shareText } from "@/lib/format";
import type { Bundle } from "@/lib/types";

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
  const max = Math.max(...ordered.map((topic) => measure(topic.share, topic.count)), 1);

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
          const value = measure(topic.share, topic.count);
          const width = Math.max(1.5, (value / max) * 100);
          const valueLabel = lowData
            ? `${topic.count} ${topic.count === 1 ? "item" : "items"}`
            : `${shareText(topic.share)}%`;

          return (
            <li
              key={String(topic.id)}
              className="border-b border-border/60 py-3 last:border-b-0"
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm leading-snug font-medium text-foreground">
                  {topic.label}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {valueLabel}
                </span>
              </div>

              {/* One series, so no legend: the section title names the measure,
                  and every bar is directly labelled above it. */}
              <div
                className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full"
                style={{ background: "var(--viz-track)" }}
                role="img"
                aria-label={`${topic.label}: ${valueLabel}`}
              >
                <div
                  className="h-full rounded-r-[4px]"
                  style={{ width: `${width}%`, background: "var(--viz-accent)" }}
                />
              </div>

              {topic.finding ? (
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {topic.finding}{" "}
                  <EvidencePills citations={topic.citations} label="Source" />
                </p>
              ) : (
                <EvidencePills
                  citations={topic.citations}
                  label="Source"
                  className="mt-2"
                />
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
