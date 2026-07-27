import { ColumnChart, type ColumnDatum } from "@/components/charts/column-chart";
import { Section } from "@/components/report/section";
import type { Bundle } from "@/lib/types";

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
  const trend = bundle.stats.trend ?? [];
  if (trend.length === 0) return null;

  const data: ColumnDatum[] = trend.map(([date, count]) => ({
    label: date,
    shortLabel: shortDate(date),
    value: count,
  }));

  return (
    <Section title="Activity">
      <ColumnChart
        data={data}
        valueName="posts"
        caption="Posts per day across the period the fetch actually covered."
      />
    </Section>
  );
}
