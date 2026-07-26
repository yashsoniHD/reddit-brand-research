/**
 * Horizontal bars for a single measure, biggest first.
 *
 * One series, so no legend: the caption names what is plotted. Every bar is
 * directly labelled with its value, which is also the secondary encoding that
 * keeps the chart readable without colour.
 */

export interface BarDatum {
  key: string;
  label: string;
  value: number;
  valueLabel: string;
}

export function BarList({
  data,
  caption,
}: {
  data: BarDatum[];
  caption: string;
}) {
  if (data.length === 0) return null;
  const rows = [...data].sort((a, b) => b.value - a.value);
  const max = Math.max(...rows.map((row) => row.value), 1);

  return (
    <figure className="m-0">
      <ul className="m-0 flex list-none flex-col gap-4 p-0">
        {rows.map((row) => {
          const width = Math.max(1.5, (row.value / max) * 100);
          return (
            <li key={row.key} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm leading-snug font-medium text-foreground">
                  {row.label}
                </span>
                <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
                  {row.valueLabel}
                </span>
              </div>
              <div
                className="h-2.5 w-full overflow-hidden rounded-full"
                style={{ background: "var(--viz-track)" }}
                role="img"
                aria-label={`${row.label}: ${row.valueLabel}`}
              >
                <div
                  className="h-full rounded-r-[4px] transition-[width] duration-500"
                  style={{ width: `${width}%`, background: "var(--viz-accent)" }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <figcaption className="mt-4 text-xs text-muted-foreground">{caption}</figcaption>
    </figure>
  );
}
