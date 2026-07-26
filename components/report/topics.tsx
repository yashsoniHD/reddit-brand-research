import { BarList, type BarDatum } from "@/components/charts/bar-list";
import { CitationList } from "@/components/report/citation-list";
import { Section } from "@/components/report/section";
import { shareText } from "@/lib/format";
import type { Bundle } from "@/lib/types";

export function Topics({ bundle }: { bundle: Bundle }) {
  const topics = bundle.analysis.topics ?? [];
  const lowData = bundle.sample.low_data;

  if (topics.length === 0) {
    return (
      <Section title="Trending topics">
        <p className="text-sm text-muted-foreground">
          No recurring topics were identified in this sample.
        </p>
      </Section>
    );
  }

  // In a thin sample a percentage implies a precision the data does not have,
  // so the measure switches to plain item counts.
  const data: BarDatum[] = topics.map((topic) => ({
    key: String(topic.id),
    label: topic.label,
    value: lowData ? topic.count : topic.share,
    valueLabel: lowData
      ? `${topic.count} ${topic.count === 1 ? "item" : "items"}`
      : `${shareText(topic.share)}%`,
  }));

  const ordered = [...topics].sort((a, b) =>
    lowData ? b.count - a.count : b.share - a.share,
  );

  return (
    <Section title="Trending topics">
      <BarList
        data={data}
        caption={
          lowData
            ? "Items mentioning each topic, from per item classifications."
            : "Share of the analysed conversation, from per item classifications."
        }
      />

      <div className="mt-6 flex flex-col gap-2">
        {ordered.map((topic, position) => (
          <details
            key={String(topic.id)}
            open={position < 2}
            className="group rounded-xl border bg-card px-4 py-3"
          >
            <summary className="flex cursor-pointer list-none items-baseline justify-between gap-4 text-sm font-medium text-foreground marker:hidden">
              <span>{topic.label}</span>
              <span className="shrink-0 text-xs font-normal tabular-nums text-muted-foreground">
                {lowData
                  ? `${topic.count} items`
                  : `${shareText(topic.share)}% of the conversation`}
              </span>
            </summary>
            <div className="mt-3">
              {topic.finding ? (
                <p className="text-sm leading-relaxed text-foreground">{topic.finding}</p>
              ) : null}
              {topic.citations.length > 0 ? (
                <>
                  <p className="mt-3 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    Evidence
                  </p>
                  <CitationList citations={topic.citations} />
                </>
              ) : null}
            </div>
          </details>
        ))}
      </div>
    </Section>
  );
}
