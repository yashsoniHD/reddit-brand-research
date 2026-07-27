import { ColumnChart, type ColumnDatum } from "@/components/charts/column-chart";
import { Note, Section } from "@/components/report/section";
import type { Bundle } from "@/lib/types";

/**
 * Past this much lag the last columns are visibly short for a reason that has
 * nothing to do with Reddit going quiet, and the chart needs to say so.
 */
const LAG_WORTH_EXPLAINING_HOURS = 24;

/** "2026-07-14" as "14 Jul", without pulling in a date library. */
function shortDate(iso: string): string {
  const parts = iso.split("-");
  if (parts.length !== 3) return iso;
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const month = months[Number(parts[1]) - 1] ?? parts[1];
  return `${Number(parts[2])} ${month}`;
}

export function ActivityTrend({ bundle }: { bundle: Bundle }) {
  const trend = bundle.stats?.trend ?? [];
  if (trend.length === 0) return null;

  const data: ColumnDatum[] = trend.map(([date, count]) => ({
    label: date,
    shortLabel: shortDate(date),
    value: count,
  }));

  const lagHours = Math.round(
    Number(bundle.stats?.data_lag_hours ?? bundle.stats?.lag_hours ?? 0),
  );
  const explainLag = Number.isFinite(lagHours) && lagHours >= LAG_WORTH_EXPLAINING_HOURS;

  return (
    <Section title="Activity">
      <ColumnChart
        data={data}
        valueName="posts"
        caption="Posts per day across the period the fetch actually covered."
      />
      {explainLag ? (
        <Note>
          The archive this reads from runs about {lagHours} hours behind Reddit, so the
          last day or two look quieter than they were. Those columns fill in later.
        </Note>
      ) : null}
    </Section>
  );
}
