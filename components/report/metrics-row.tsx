import { compact, thousands } from "@/lib/format";
import type { Bundle } from "@/lib/types";

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p className="mt-1.5 text-xl leading-tight font-semibold text-foreground">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function MetricsRow({ bundle }: { bundle: Bundle }) {
  const sample = bundle.sample;
  const engagement = bundle.stats.engagement;
  const range = bundle.stats.date_range;

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Metric
        label="Posts analysed"
        value={
          sample.posts_truncated
            ? `${sample.posts_analysed} of ${sample.posts_fetched}`
            : String(sample.posts_analysed)
        }
      />
      <Metric
        label="Comments analysed"
        value={
          sample.comments_truncated
            ? `${sample.comments_analysed} of ${sample.comments_fetched}`
            : String(sample.comments_analysed)
        }
      />
      <Metric
        label="Actual range"
        value={range.short_label || range.label || "n/a"}
        hint={`Oldest to newest post: ${range.label || "n/a"}`}
      />
      <Metric
        label="Total engagement"
        value={`${compact(engagement.score)} upvotes`}
        hint={`${thousands(engagement.comments)} comments across ${thousands(
          sample.posts_fetched,
        )} posts fetched`}
      />
    </div>
  );
}
