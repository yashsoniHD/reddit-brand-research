import { thousands, truncate } from "@/lib/format";
import type { Bundle } from "@/lib/types";

const SUMMARY_QUERY_CHARS = 80;

/**
 * One compact line above the ideas: what was asked, where we read, how far
 * back, how much. It replaces the old chip row, which spent a whole band of the
 * page restating the form.
 */
export function SummaryStrip({ bundle }: { bundle: Bundle }) {
  const { inputs, sample } = bundle;
  const communities = inputs.subreddits.map((name) => `r/${name}`).join(", ");

  const facts = [
    communities || "no communities",
    `last ${inputs.days} days`,
    `${thousands(sample.items_analysed)} items read`,
  ];

  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-sm leading-snug font-medium text-foreground">
        {truncate(inputs.query, SUMMARY_QUERY_CHARS)}
      </p>
      <p className="text-xs text-muted-foreground">
        {facts.join(" · ")}
        <span className="hidden sm:inline"> · {bundle.generated_at}</span>
      </p>
    </div>
  );
}
