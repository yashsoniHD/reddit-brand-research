import { ActivityTrend } from "@/components/report/activity-trend";
import { ClaimColumns } from "@/components/report/claim-columns";
import { Communities } from "@/components/report/communities";
import { ContentIdeas } from "@/components/report/content-ideas";
import { ExportButtons } from "@/components/report/export-buttons";
import { Methodology } from "@/components/report/methodology";
import { Drawer } from "@/components/report/section";
import { Sentiment } from "@/components/report/sentiment";
import { SourceData } from "@/components/report/source-data";
import { SummaryStrip } from "@/components/report/summary-strip";
import { Topics } from "@/components/report/topics";
import { thousands } from "@/lib/format";
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
      ? "border-l-[var(--brand)] bg-accent/60"
      : "border-l-[var(--viz-neg)] bg-secondary";
  return (
    <p className={`rounded-lg border-l-2 px-4 py-2.5 text-sm leading-relaxed ${styles}`}>
      {children}
    </p>
  );
}

/**
 * Ideas first, research second.
 *
 * The order is deliberate and is the whole point of this layout: the ideas are
 * the product, so nothing gets to sit above them except the one line saying what
 * was searched. Everything that supports them lives in a visibly quieter zone
 * below, and the raw material is collapsed until asked for.
 */
export function ReportView({ bundle }: { bundle: Bundle }) {
  const summary = bundle.analysis.executive_summary;

  return (
    <article className="flex flex-col gap-8">
      <header className="flex flex-col gap-4">
        {bundle.is_sample ? (
          <Callout tone="info">
            This is a sample report, generated from real Reddit data through the normal
            pipeline. Run your own search above to see your version.
          </Callout>
        ) : null}

        <div className="flex flex-wrap items-end justify-between gap-4">
          <SummaryStrip bundle={bundle} />
          <ExportButtons bundle={bundle} />
        </div>

        {bundle.notices.map((notice, index) => (
          <Callout key={`${index}-${notice}`} tone="warning">
            {notice}
          </Callout>
        ))}

        {bundle.sample.low_data ? (
          <Callout tone="warning">
            Only {bundle.sample.items_analysed} items were found for this search, so the
            results are directional rather than conclusive.
          </Callout>
        ) : null}
      </header>

      {/* ── The product ──────────────────────────────────────────────────── */}
      <section aria-labelledby="ideas-heading" className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <h2
            id="ideas-heading"
            className="text-xl font-semibold tracking-tight text-foreground"
          >
            Content ideas
          </h2>
          {summary ? (
            <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
              {summary}
            </p>
          ) : null}
        </div>

        <ContentIdeas bundle={bundle} />

        <p className="text-xs text-muted-foreground">
          Click any idea to get a full post written from it and the sources behind it.
        </p>
      </section>

      {/* ── The quieter research zone ────────────────────────────────────── */}
      <section
        aria-labelledby="research-heading"
        className="surface-hush mt-2 flex flex-col gap-8 px-5 py-6 sm:px-7 sm:py-8"
      >
        <div className="flex flex-col gap-3">
          <h2 id="research-heading" className="h-zone text-foreground">
            What the research says
          </h2>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Every idea above traces back to these findings, and every finding traces back
            to {thousands(bundle.sample.items_analysed)} posts and comments read in full.
          </p>
          <Communities bundle={bundle} />
        </div>

        <Topics bundle={bundle} />
        <Sentiment bundle={bundle} />
        <ClaimColumns
          painPoints={bundle.analysis.pain_points}
          questions={bundle.analysis.questions}
        />
        <ActivityTrend bundle={bundle} />
      </section>

      {/* ── The raw material, closed until asked for ─────────────────────── */}
      <div className="flex flex-col gap-3">
        <Drawer
          summary="Source data"
          hint={`${thousands(bundle.posts.length)} posts · ${thousands(
            bundle.comments.length,
          )} comments`}
        >
          <SourceData bundle={bundle} />
        </Drawer>
        <Methodology bundle={bundle} />
      </div>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Source: public Reddit data via the {bundle.source}. Report generated{" "}
        {bundle.generated_at}. Analysis: {bundle.model_label}.
      </p>
    </article>
  );
}
