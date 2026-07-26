import type { Bundle } from "@/lib/types";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-2 sm:grid-cols-[11rem_1fr] sm:gap-4">
      <dt className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="text-sm leading-relaxed text-foreground">{children}</dd>
    </div>
  );
}

export function Methodology({ bundle }: { bundle: Bundle }) {
  const { sample, inputs } = bundle;
  const range = bundle.stats.date_range;

  return (
    <details className="rounded-xl border bg-card px-5 py-4">
      <summary className="cursor-pointer list-none text-sm font-semibold text-foreground marker:hidden">
        Methodology and limitations
      </summary>
      <dl className="mt-3 divide-y divide-border">
        <Row label="Communities">
          {inputs.subreddits.map((name) => `r/${name}`).join(", ") || "None"}
        </Row>
        <Row label="Discovery">
          A model proposes candidate subreddits for your query. Each one is checked
          against the Reddit archive to confirm it exists and is public before it is
          read, so the model proposes and the archive verifies.
        </Row>
        <Row label="Window">
          Requested: last {inputs.days} days. Actual coverage: {range.label || "n/a"}.
        </Row>
        <Row label="Posts">
          {sample.posts_fetched} fetched, {sample.posts_analysed} analysed.
        </Row>
        <Row label="Comments">
          {sample.comments_fetched} fetched, {sample.comments_analysed} analysed.
        </Row>
        <Row label="Truncation">
          When a community exceeds the sample size, the most recent posts and the top
          comments by score are the ones analysed.
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
        <Row label="Citations">Every link resolves to the archived source item.</Row>
        <Row label="Model">{bundle.model_label}</Row>
        <Row label="Generated">
          {bundle.generated_at} in {bundle.elapsed_seconds} seconds.
        </Row>
      </dl>
    </details>
  );
}
