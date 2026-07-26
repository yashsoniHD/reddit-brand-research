import { ActivityTrend } from "@/components/report/activity-trend";
import { ClaimColumns } from "@/components/report/claim-columns";
import { Communities } from "@/components/report/communities";
import { ContentIdeas } from "@/components/report/content-ideas";
import { ExportButtons } from "@/components/report/export-buttons";
import { MetricsRow } from "@/components/report/metrics-row";
import { Methodology } from "@/components/report/methodology";
import { Sentiment } from "@/components/report/sentiment";
import { SourceData } from "@/components/report/source-data";
import { SummaryChips } from "@/components/report/summary-chips";
import { Topics } from "@/components/report/topics";
import { Separator } from "@/components/ui/separator";
import type { Bundle } from "@/lib/types";

function Callout({
  tone,
  children,
}: {
  tone: "info" | "warning";
  children: React.ReactNode;
}) {
  const styles =
    tone === "info"
      ? "border-l-[var(--brand)] bg-accent/60 text-foreground"
      : "border-l-[var(--viz-neg)] bg-secondary text-foreground";
  return (
    <p className={`rounded-lg border-l-2 px-4 py-3 text-sm leading-relaxed ${styles}`}>
      {children}
    </p>
  );
}

export function ReportView({ bundle }: { bundle: Bundle }) {
  const summary = bundle.analysis.executive_summary;

  return (
    <article className="flex flex-col gap-10">
      <header className="flex flex-col gap-4">
        {bundle.is_sample ? (
          <Callout tone="info">
            This is a sample report, generated from real Reddit data through the normal
            pipeline. Run your own search above to see your query&rsquo;s version.
          </Callout>
        ) : null}

        <SummaryChips bundle={bundle} />
        <ExportButtons bundle={bundle} />

        {bundle.notices.map((notice, index) => (
          <Callout key={`${index}-${notice}`} tone="warning">
            {notice}
          </Callout>
        ))}

        {bundle.sample.low_data ? (
          <Callout tone="warning">
            Only {bundle.sample.items_analysed} items were found for this window, so the
            results are directional rather than conclusive. Try a 30 day window.
          </Callout>
        ) : null}
      </header>

      <Communities bundle={bundle} />

      {summary ? (
        <section>
          <h2 className="text-lg font-semibold tracking-tight text-foreground">
            Executive summary
          </h2>
          <p className="mt-3 border-l-2 pl-4 text-base leading-relaxed text-foreground [border-color:var(--brand)]">
            {summary}
          </p>
        </section>
      ) : null}

      <MetricsRow bundle={bundle} />
      <Separator />
      <Topics bundle={bundle} />
      <Separator />
      <Sentiment bundle={bundle} />
      <Separator />
      <ClaimColumns
        painPoints={bundle.analysis.pain_points}
        questions={bundle.analysis.questions}
      />
      <Separator />
      <ContentIdeas bundle={bundle} />
      <Separator />
      <ActivityTrend bundle={bundle} />
      <Separator />
      <SourceData bundle={bundle} />
      <Separator />
      <Methodology bundle={bundle} />

      <p className="text-xs leading-relaxed text-muted-foreground">
        Source: public Reddit data via the {bundle.source}. Report generated{" "}
        {bundle.generated_at}. Analysis: {bundle.model_label}.
      </p>
    </article>
  );
}
