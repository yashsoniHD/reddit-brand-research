import { Callout } from "@/components/callout";
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

/**
 * One of the four counts the research zone opens with.
 *
 * Source order is term then definition, as a description list requires;
 * `flex-col-reverse` puts the number on top where the eye wants it.
 */
function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col-reverse gap-1">
      {/* `h-quiet` rather than another hand-tuned small grey: the app already
          owns one label style, and this was a fourth variant of it. */}
      <dt className="h-quiet">{label}</dt>
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
    // Zones are a level apart from the sections inside them, so the article
    // gap has to be wider than the research zone's own `gap-8`. At the same
    // value the two levels read as peers.
    <article className="flex flex-col gap-12">
      <header className="flex flex-col gap-5">
        {bundle.is_sample ? (
          <Callout tone="sample">
            <span className="font-semibold text-foreground">Sample report.</span> Generated
            from real Reddit data through the normal pipeline. Run your own brief above to
            see your version.
          </Callout>
        ) : null}

        {/* Notices sit above the masthead rule, not below it: a warning stranded
            after the divider reads as a caption on the next zone rather than as
            a qualification of this report. The rule stays the last thing in the
            header, which is the only job it has. */}
        {notices.map((notice, index) => (
          <Callout key={`${index}-${notice}`} tone="warning">
            {notice}
          </Callout>
        ))}

        {sample?.low_data ? (
          <Callout tone="warning">
            Only {sample.items_analysed} items were found for this brief, so the results
            are directional rather than conclusive.
          </Callout>
        ) : null}

        {/* `items-start`, not `items-end`: bottom-aligning tied the export
            cluster to the masthead's quietest 12px line, which is the one thing
            on the page it has nothing to do with. */}
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4 border-b pb-6">
          <SummaryStrip bundle={bundle} />
          <ExportButtons bundle={bundle} />
        </div>
      </header>

      {/* ── The product ──────────────────────────────────────────────────── */}
      <section aria-labelledby="ideas-heading" className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          {/* `h-zone`, not `t-title`: the document title in the masthead is the
              only `t-title` on the first screen. Two of them six rems apart gave
              the eye two entry points and it picked neither. This now pairs with
              "What the research says", which is the same level. */}
          <h2 id="ideas-heading" className="h-zone text-foreground">
            Content ideas
          </h2>
          {summary ? (
            // Capped near 70ch: the summary is the longest prose on the page and
            // ran past 100 characters a line, which is past the point a reader
            // reliably finds the next line.
            <p className="t-lead max-w-[70ch] text-foreground-soft">{summary}</p>
          ) : null}
          {/* The product's primary affordance, said where the reader meets the
              grid. It used to sit under the cards, below the fold on any report
              with more than three ideas. */}
          <p className="t-small text-muted-foreground">
            Click any idea to get a full post written from it and the sources behind it.
          </p>
        </div>

        {/* Informational, not a failure: the ideas are still worth reading, but
            the reader deserves to know what they were built from. */}
        {thinEvidence ? (
          <Callout tone="info">
            Only {strongCount} of {thousands(rated)} rated items were strongly on-topic.
            These ideas lean on adjacent conversations. A narrower brief or hand-picked
            communities may sharpen them.
          </Callout>
        ) : null}

        <ContentIdeas bundle={bundle} initialDrafts={initialDrafts} />
      </section>

      {/* ── The quieter research zone ──────────────────────────────────────
          Separated by a rule and space rather than a tinted slab. The slab was
          the heaviest possible way to say "this part is secondary": a
          full-width fill, and the largest single surface on the page, so
          whatever it was tinted it dominated everything above it. Quiet is
          carried by the type instead, which is what the ordering comment below
          says this zone is for.

          `gap-8` here against the article's `gap-12`: the five subsections are
          one level below the zones, and they used to share the zone value, which
          flattened the two into peers. No `mt` on top of it, because the
          article gap is already the space above this rule. */}
      <section
        aria-labelledby="research-heading"
        className="rule flex flex-col gap-8 border-t pt-8"
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <h2 id="research-heading" className="h-zone text-foreground">
              What the research says
            </h2>
            {/* The citation mechanic is explained here because this is where a
                reader first meets a pill, and an unexplained numbered chip is
                just a chip. */}
            <p className="t-small max-w-[70ch] text-muted-foreground">
              Every idea above traces back to these findings. The numbered pills next to a
              claim link straight to the Reddit thread it came from.
            </p>
          </div>

          {/* The four numbers in this report describe four different pools, and
              run together in a sentence they look like they contradict each
              other. Laid out as a funnel, left to right, they read as the
              stages they are. */}
          <dl className="rule grid grid-cols-2 gap-x-4 gap-y-5 border-y py-5 sm:grid-cols-4">
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
