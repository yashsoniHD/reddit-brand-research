import { Drawer } from "@/components/report/section";
import { compact, thousands } from "@/lib/format";
import { bundleContentTypes, type Bundle, type ContentType } from "@/lib/types";

const CONTENT_TYPE_LABELS: Record<ContentType, string> = {
  text: "text post",
  carousel: "carousel",
  video: "video script",
  poll: "poll",
  article: "long-form article",
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[9rem_1fr] sm:gap-6">
      <dt className="h-quiet sm:pt-0.5">{label}</dt>
      <dd className="m-0 text-[0.8125rem] leading-relaxed text-foreground-soft">
        {children}
      </dd>
    </div>
  );
}

export function Methodology({ bundle }: { bundle: Bundle }) {
  const sample = bundle.sample;
  const inputs = bundle.inputs ?? { query: "", profile: "", subreddits: [] };
  const range = bundle.stats?.date_range;
  const engagement = bundle.stats?.engagement;
  const analysed = engagement?.analysed;
  const lagHours = bundle.stats?.data_lag_hours ?? bundle.stats?.lag_hours;
  const contentTypes = bundleContentTypes(bundle.inputs).map(
    (type) => CONTENT_TYPE_LABELS[type],
  );

  return (
    <Drawer summary="Methodology and limitations" hint={bundle.model_label}>
      <dl className="divide-y divide-border">
        <Row label="Communities">
          {(inputs.subreddits ?? []).map((name) => `r/${name}`).join(", ") || "None"}
        </Row>
        <Row label="Discovery">
          A model proposes candidate subreddits for your search. Each one is checked
          against the Reddit archive to confirm it exists and is public before it is
          read, so the model proposes and the archive verifies.
        </Row>
        <Row label="Window">
          Requested: last {inputs.days ?? 30} days. Actual coverage:{" "}
          {range?.label || "n/a"}.
        </Row>
        {contentTypes.length > 0 ? (
          <Row label={contentTypes.length === 1 ? "Content type" : "Content types"}>
            {contentTypes.length === 1
              ? `Ideas were written for one format: ${contentTypes[0]}.`
              : `Ideas were written for these formats only: ${contentTypes.join(", ")}.`}
          </Row>
        ) : null}
        <Row label="Posts">
          {thousands(sample?.posts_fetched)} fetched, {thousands(sample?.posts_analysed)}{" "}
          analysed.
        </Row>
        <Row label="Comments">
          {thousands(sample?.comments_fetched)} fetched,{" "}
          {thousands(sample?.comments_analysed)} analysed.
        </Row>
        <Row label="Engagement">
          {compact(engagement?.score)} upvotes and {compact(engagement?.comments)}{" "}
          comments across the posts fetched.
          {analysed
            ? ` Within the analysed sample: ${compact(analysed.upvotes)} upvotes and ${compact(analysed.comments)} comments.`
            : ""}
        </Row>
        <Row label="Selection">
          {sample?.selection?.method === "relevance+engagement"
            ? `Every candidate was rated for relevance to your search by the model (${thousands(sample.selection.triaged)} items rated), then ranked in code by relevance, community engagement and recency.`
            : sample?.selection?.method === "relevance-off"
              ? "The relevance filter was turned off for this run, so items were ranked in code by community engagement and recency."
              : "Items were ranked in code by community engagement and recency; the relevance rating was unavailable for this run."}{" "}
          No single community takes more than half the sample while others have items
          to offer.
        </Row>
        <Row label="Truncation">
          When more was fetched than the sample can hold, the top-ranked posts and
          comments are the ones analysed.
          {sample?.capacity_limited
            ? " This run was capped by the current analysis capacity tier."
            : ""}
        </Row>
        <Row label="Exclusions">
          Posts and comments deleted or removed by their authors or moderators are
          skipped, as are duplicates.
        </Row>
        <Row label="Freshness">
          Newest item in this sample: {range?.newest_iso || "n/a"}, roughly{" "}
          {lagHours ?? 0} hours behind live Reddit when this report was generated.
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
