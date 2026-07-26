import { truncate } from "@/lib/format";
import type { Bundle } from "@/lib/types";

const SUMMARY_QUERY_CHARS = 70;

/** The run's inputs at a glance, so a report is never read out of context. */
export function SummaryChips({ bundle }: { bundle: Bundle }) {
  const inputs = bundle.inputs;
  const chips = [
    truncate(inputs.query, SUMMARY_QUERY_CHARS),
    inputs.platform_label,
    inputs.subreddits.map((name) => `r/${name}`).join(", "),
    `Last ${inputs.days} days`,
    bundle.generated_at,
  ].filter(Boolean);

  return (
    <ul className="flex list-none flex-wrap gap-2 p-0">
      {chips.map((chip, index) => (
        <li
          key={`${index}-${chip}`}
          className="rounded-full bg-secondary px-3 py-1 text-xs text-secondary-foreground"
        >
          {chip}
        </li>
      ))}
    </ul>
  );
}
