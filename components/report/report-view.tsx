import { FlaskConical, Info, TriangleAlert } from "lucide-react";

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

/** Below this many strongly on-topic items the ideas are working from scraps. */
const THIN_EVIDENCE_THRESHOLD = 10;

function Callout({
  tone,
  children,
}: {
  tone: "info" | "warning";
  children: React.ReactNode;
}) {
  const info = tone === "info";
  const Icon = info ? Info : TriangleAlert;
  return (
    <p
      className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-[0.8125rem] leading-relaxed ${
        info
          ? "border-[var(--brand-line)] bg-[var(--brand-soft)] text-foreground"
          : "border-destructive/30 bg-destructive/8 text-foreground"
      }`}
    >
      {/* An icon as well as a tint, so the tone survives greyscale. */}
      <Icon
        aria-hidden="true"
        strokeWidth={2}
        className={`mt-px size-4 shrink-0 ${
          info ? "text-[var(--brand-ink)]" : "text-destructive"
        }`}
      />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/**
 * One of the four counts the research zone opens with.
 *
 * Source order is term then definition, as a description list requires;
 * `flex-col-reverse` puts the number on top where the eye wants it.
 */
function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col-reverse gap-0.5">
      <dt className="text-[0.6875rem] leading-snug text-muted-foreground">{label}</dt>
      <dd className="stat m-0 text-foreground">{value}</dd>
    </div>
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
export function ReportView({
  bundle,
  initialDrafts,
}: {
  bundle: Bundle;
  /** Posts already saved for this report, keyed by idea index as a string.
   *  Passed straight down to the ideas grid, which owns the draft cache. */
  initialDrafts?: Record<string, string>;
}) {
  const analysis = bundle.analysis;
  const sample = bundle.sample;
  const summary = analysis?.executive_summary;
  const notices = bundle.notices ?? [];
  const posts = bundle.posts ?? [];
  const comments = bundle.comments ?? [];

  const selection = sample?.selection;
  const strongCount = selection?.relevance_counts?.["2"];
  const rated = selection?.triaged ?? 0;
  const thinEvidence =
    typeof strongCount === "number" && rated > 0 && strongCount < THIN_EVIDENCE_THRESHOLD;

  return (
    <article className="flex flex-col gap-8">
      <header className="flex flex-col gap-5">
        {bundle.is_sample ? (
          <p className="flex items-start gap-2.5 rounded-xl border border-[var(--brand-line)] bg-[var(--brand-soft)] px-4 py-3 text-[0.8125rem] leading-relaxed">
            <FlaskConical
              aria-hidden="true"
              strokeWidth={2}
              className="mt-px size-4 shrink-0 text-[var(--brand-ink)]"
            />
            <span>
              <span className="font-semibold text-foreground">Sample report.</span>{" "}
              Generated from real Reddit data through the normal pipeline. Run your own
              search above to see your version.
            </span>
          </p>
        ) : null}

        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 border-b pb-6">
          <SummaryStrip bundle={bundle} />
          <ExportButtons bundle={bundle} />
        </div>

        {notices.map((notice, index) => (
          <Callout key={`${index}-${notice}`} tone="warning">
            {notice}
          </Callout>
        ))}

        {sample?.low_data ? (
          <Callout tone="warning">
            Only {sample.items_analysed} items were found for this search, so the results
            are directional rather than conclusive.
          </Callout>
        ) : null}
      </header>

      {/* ── The product ──────────────────────────────────────────────────── */}
      <section aria-labelledby="ideas-heading" className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <h2 id="ideas-heading" className="t-title text-foreground">
            Content ideas
          </h2>
          {summary ? (
            <p className="max-w-3xl text-[0.9375rem] leading-relaxed text-foreground-soft">
              {summary}
            </p>
          ) : null}
        </div>

        {/* Informational, not a failure: the ideas are still worth reading, but
            the reader deserves to know what they were built from. */}
        {thinEvidence ? (
          <Callout tone="info">
            Only {strongCount} of {thousands(rated)} rated items were strongly on-topic.
            These ideas lean on adjacent conversations. A narrower query or hand-picked
            communities may sharpen them.
          </Callout>
        ) : null}

        <ContentIdeas bundle={bundle} initialDrafts={initialDrafts} />

        <p className="text-xs text-muted-foreground">
          Click any idea to get a full post written from it and the sources behind it.
        </p>
      </section>

      {/* ── The quieter research zone ────────────────────────────────────── */}
      <section
        aria-labelledby="research-heading"
        className="surface-hush mt-2 flex flex-col gap-8 px-5 py-6 sm:px-7 sm:py-8"
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <h2 id="research-heading" className="h-zone text-foreground">
              What the research says
            </h2>
            <p className="text-xs leading-relaxed text-muted-foreground">
              Every idea above traces back to these findings.
            </p>
          </div>

          {/* The four numbers in this report describe four different pools, and
              run together in a sentence they look like they contradict each
              other. Laid out as a funnel, left to right, they read as the
              stages they are. */}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-4 border-y py-4 sm:grid-cols-4">
            <Stat value={thousands(sample?.posts_fetched)} label="Posts fetched" />
            <Stat value={thousands(sample?.comments_fetched)} label="Comments fetched" />
            <Stat
              value={rated > 0 ? thousands(rated) : "n/a"}
              label="Rated for relevance"
            />
            <Stat value={thousands(sample?.items_analysed)} label="Read in full" />
          </dl>

          <Communities bundle={bundle} />
        </div>

        <Topics bundle={bundle} />
        <Sentiment bundle={bundle} />
        <ClaimColumns
          painPoints={analysis?.pain_points ?? []}
          questions={analysis?.questions ?? []}
        />
        <ActivityTrend bundle={bundle} />
      </section>

      {/* ── The raw material, closed until asked for ─────────────────────── */}
      <div className="flex flex-col gap-3">
        <Drawer
          summary="Source data"
          hint={`${thousands(posts.length)} posts · ${thousands(comments.length)} comments`}
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
