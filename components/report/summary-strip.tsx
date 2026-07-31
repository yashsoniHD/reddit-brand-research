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

  const window = bundle.stats?.date_range?.short_label;
  const itemsRead = thousands(sample?.items_analysed);

  /**
   * `num` is tabular figures and tight tracking, so it belongs on the digits
   * and nowhere else. It used to sit on the whole `<li>`, which applied number
   * spacing to prose like "no communities" and to community names.
   */
  const facts: Array<{ icon: typeof Users; key: string; value: React.ReactNode }> = [
    { icon: Users, key: "communities", value: communities || "no communities" },
    // No window claim in the fallback: a search run has no requested window, so
    // naming one would be inventing coverage the report cannot vouch for.
    {
      icon: CalendarRange,
      key: "window",
      value: window ? <span className="num">{window}</span> : "dates n/a",
    },
    {
      icon: FileText,
      key: "items",
      value: (
        <>
          <span className="num">{itemsRead}</span> items read
        </>
      ),
    },
    ...(activePhrase
      ? [{ icon: Quote, key: "phrase", value: `mentions ${activePhrase}` }]
      : []),
  ];

  return (
    <div className="flex min-w-0 flex-col gap-2.5">
      <span className="h-quiet">Content research report</span>

      <h2 className="t-title text-balance text-foreground">{title}</h2>

      {/* `t-body` rather than a hand-typed 15px: the scale owns this size, and
          it carries its own leading, so the old `leading-snug` goes with it. */}
      {query ? (
        <p className="t-body max-w-[68ch] text-foreground-soft">{query}</p>
      ) : null}

      <ul className="mt-0.5 flex list-none flex-wrap items-center gap-x-4 gap-y-1.5 p-0">
        {facts.map((fact) => (
          <li
            key={fact.key}
            className="flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <fact.icon
              aria-hidden="true"
              strokeWidth={2.25}
              className="size-3.5 shrink-0 text-[var(--brand)]"
            />
            {fact.value}
          </li>
        ))}
        <li className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
          <Clock
            aria-hidden="true"
            strokeWidth={2.25}
            className="size-3.5 shrink-0 text-[var(--brand)]"
          />
          <span className="num">{bundle.generated_at}</span>
        </li>
      </ul>
    </div>
  );
}
