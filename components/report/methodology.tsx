import { Drawer } from "@/components/report/section";
import { compact, thousands } from "@/lib/format";
import type { Bundle } from "@/lib/types";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-2 sm:grid-cols-[10rem_1fr] sm:gap-4">
      <dt className="h-quiet">{label}</dt>
      <dd className="text-sm leading-relaxed text-foreground">{children}</dd>
    </div>
  );
}

export function Methodology({ bundle }: { bundle: Bundle }) {
  const { sample, inputs } = bundle;
  const range = bundle.stats.date_range;
  const engagement = bundle.stats.engagement;

  return (
    <Drawer summary="Methodology and limitations" hint={bundle.model_label}>
      <dl className="divide-y divide-border/60">
        <Row label="Communities">
          {inputs.subreddits.map((name) => `r/${name}`).join(", ") || "None"}
        </Row>
        <Row label="Discovery">
          A model proposes candidate subreddits for your search. Each one is checked
          against the Reddit archive to confirm it exists and is public before it is
          read, so the model proposes and the archive verifies.
        </Row>
        <Row label="Window">
          The last 30 days are always read. Actual coverage: {range.label || "n/a"}.
        </Row>
        <Row label="Posts">
          {thousands(sample.posts_fetched)} fetched, {thousands(sample.posts_analysed)}{" "}
          analysed.
        </Row>
        <Row label="Comments">
          {thousands(sample.comments_fetched)} fetched,{" "}
          {thousands(sample.comments_analysed)} analysed.
        </Row>
        <Row label="Engagement">
          {compact(engagement.score)} upvotes and {compact(engagement.comments)} comments
          across the posts fetched.
        </Row>
        <Row label="Selection">
          {sample.selection?.method === "relevance+engagement"
            ? `Every candidate was rated for relevance to your search by the model (${thousands(sample.selection.triaged)} items rated), then ranked in code by relevance, community engagement and recency.`
            : "Items were ranked in code by community engagement and recency; the relevance rating was unavailable for this run."}{" "}
          No single community takes more than half the sample while others have items
          to offer.
        </Row>
        <Row label="Truncation">
          When more was fetched than the sample can hold, the top-ranked posts and
          comments are the ones analysed.
          {sample.capacity_limited
            ? " This run was capped by the current analysis capacity tier."
            : ""}
        </Row>
        <Row label="Exclusions">
          Posts and comments deleted or removed by their authors or moderators are
          skipped, as are duplicates.
        </Row>
        <Row label="Freshness">
          Newest item in this sample: {range.newest_iso || "n/a"}, roughly{" "}
          {bundle.stats.lag_hours} hours behind live Reddit when this report was
          generated.
        </Row>
        <Row label="Numbers">
          Topic volumes and sentiment percentages are computed from per item
          classifications, not estimated by the model.
        </Row>
        <Row label="Citations">Every evidence pill resolves to the archived source.</Row>
        <Row label="Model">{bundle.model_label}</Row>
        <Row label="Generated">
          {bundle.generated_at} in {bundle.elapsed_seconds} seconds.
        </Row>
      </dl>
    </Drawer>
  );
}
