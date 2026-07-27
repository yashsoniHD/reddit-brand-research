/**
 * One stacked bar for a diverging three way split.
 *
 * Colours are the validated diverging trio: accent blue, neutral gray,
 * critical red, with a 2px surface gap doing the separating rather than a
 * stroke. The legend below always names each segment, so identity never
 * depends on colour alone.
 */

export interface StackedSegment {
  key: string;
  label: string;
  percent: number;
  count: number;
  colour: string;
}

export function StackedBar({ segments }: { segments: StackedSegment[] }) {
  const visible = segments.filter((segment) => segment.percent > 0);

  return (
    <figure className="m-0">
      <div
        className="flex h-7 w-full overflow-hidden rounded-lg"
        style={{ background: "var(--viz-track)", gap: 2 }}
        role="img"
        aria-label={segments
          .map((segment) => `${segment.label} ${segment.percent} percent`)
          .join(", ")}
      >
        {visible.map((segment) => (
          <span
            key={segment.key}
            className="block h-full first:rounded-l-lg last:rounded-r-lg"
            style={{ width: `${segment.percent}%`, background: segment.colour }}
          />
        ))}
      </div>

      {/* Every segment is named and numbered here, so nothing in the bar above
          depends on the reader telling blue from grey. */}
      <ul className="mt-3 flex list-none flex-wrap gap-x-5 gap-y-2 p-0 text-sm">
        {segments.map((segment) => (
          <li key={segment.key} className="flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 translate-y-px rounded-full"
              style={{ background: segment.colour }}
            />
            <span className="text-foreground-soft">{segment.label}</span>
            <span className="num font-semibold text-foreground">{segment.percent}%</span>
            <span className="num text-xs text-muted-foreground">({segment.count})</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
