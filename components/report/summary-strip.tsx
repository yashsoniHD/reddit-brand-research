import { CalendarRange, Clock, FileText, Quote, Users } from "lucide-react";

import { joinQuoted, reportTitle, thousands, truncate } from "@/lib/format";
import type { Bundle } from "@/lib/types";

const SUMMARY_QUERY_CHARS = 90;

/**
 * The report's masthead: what it is called, what was asked, and the four facts
 * that qualify every number below it.
 *
 * The title is the engine's generated one when there is one, so a report opened
 * from history or from a link announces what it is rather than repeating the
 * raw search string as a headline. The search itself stays underneath, because
 * it is the thing the numbers describe.
 *
 * The facts are icon-led rather than run together with middots: this is the
 * line a reader checks before trusting anything else on the page, so scanning
 * it should not require reading it.
 */
export function SummaryStrip({ bundle }: { bundle: Bundle }) {
  const inputs = bundle.inputs ?? { query: "", profile: "", subreddits: [] };
  const sample = bundle.sample;
  const communities = (inputs.subreddits ?? []).map((name) => `r/${name}`).join(", ");
  const title = reportTitle(bundle);
  // Only worth its own line when the title is the engine's rather than the
  // query itself: two near-identical truncations of one sentence read as a bug.
  const generated = Boolean(String(bundle.analysis?.report_title ?? "").trim());
  const query = generated ? truncate(inputs.query, SUMMARY_QUERY_CHARS) : "";
  // Only worth a fact once it actually shaped the sample: a dropped filter
  // (mode "unfiltered") did not, so it stays out of this strip and lives only
  // in the methodology drawer.
  const filter = sample?.filter;
  const activePhrase =
    filter && filter.mode !== "unfiltered" && filter.phrases.length > 0
      ? joinQuoted(filter.phrases)
      : "";

  const facts = [
    { icon: Users, value: communities || "no communities" },
    { icon: CalendarRange, value: bundle.stats?.date_range?.short_label || "last 30 days" },
    { icon: FileText, value: `${thousands(sample?.items_analysed)} items read` },
    ...(activePhrase ? [{ icon: Quote, value: `mentions ${activePhrase}` }] : []),
  ];

  return (
    <div className="flex min-w-0 flex-col gap-2.5">
      <span className="h-quiet">Content research report</span>

      <h2 className="t-title text-balance text-foreground">{title}</h2>

      {query ? (
        <p className="max-w-2xl text-[0.9375rem] leading-snug text-foreground-soft">
          {query}
        </p>
      ) : null}

      <ul className="mt-0.5 flex list-none flex-wrap items-center gap-x-4 gap-y-1.5 p-0">
        {facts.map((fact) => (
          <li
            key={fact.value}
            className="num flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <fact.icon
              aria-hidden="true"
              strokeWidth={2}
              className="size-3.5 shrink-0 text-[var(--brand)]"
            />
            {fact.value}
          </li>
        ))}
        <li className="num hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
          <Clock
            aria-hidden="true"
            strokeWidth={2}
            className="size-3.5 shrink-0 text-[var(--brand)]"
          />
          {bundle.generated_at}
        </li>
      </ul>
    </div>
  );
}
