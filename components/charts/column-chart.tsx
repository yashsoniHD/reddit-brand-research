"use client";

import { useRef, useState } from "react";

import { thousands } from "@/lib/format";

/**
 * Daily counts as columns: one series, one baseline, one y scale.
 *
 * Values are not labelled on every column (that reads as noise across a
 * 30 day window), so the hover tooltip and the accessible table below carry
 * the numbers the axis does not.
 */

export interface ColumnDatum {
  /** The machine form, e.g. "2026-07-17". Only the data table shows this. */
  label: string;
  /** The reader form, e.g. "17 Jul". Everything a person sees uses this. */
  shortLabel: string;
  value: number;
}

const CHART_HEIGHT = 168;
const GAP = 2;
/**
 * A short date is roughly 3.2em at the axis size, so an axis label is nudged
 * half that left of the column centre it names. Approximate by design: it is
 * clamped to the plot below, so the worst case is a label a few pixels off
 * centre rather than one hanging past the end of the chart.
 */
const LABEL_EM = 3.2;

/**
 * The top of the scale, always an even number of steps so the midpoint tick
 * lands on a whole count rather than on "7.5 posts". Each rung is the smallest
 * even step that still keeps the top close to the data.
 */
function tickStep(max: number): number {
  if (max <= 6) return 2;
  if (max <= 20) return 4;
  if (max <= 60) return 10;
  if (max <= 200) return 20;
  return 100;
}

/** "1 post" rather than "1 posts". The value names here are simple plurals. */
function unitName(value: number, valueName: string): string {
  return value === 1 ? valueName.replace(/s$/, "") : valueName;
}

export function ColumnChart({
  data,
  caption,
  valueName,
}: {
  data: ColumnDatum[];
  caption: string;
  valueName: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  // One tab stop for the whole series, arrow keys within it. As 31 separate
  // stops the chart was a keyboard trap in all but name, and at 375px each
  // column is under 10px wide, so tabbing through them navigated nothing a
  // reader could see.
  const [roving, setRoving] = useState(0);
  const columns = useRef<(HTMLButtonElement | null)[]>([]);

  if (data.length === 0) {
    // Callers own the domain wording for an empty series, but a chart that
    // renders nothing at all takes its whole section down with it silently.
    return (
      <p className="text-xs text-muted-foreground">
        No data was available to chart.
      </p>
    );
  }

  const max = Math.max(...data.map((point) => point.value), 1);
  const step = tickStep(max);
  const top = Math.ceil(max / step) * step;
  const mid = top / 2;
  const slot = 100 / data.length;
  const activePoint = active === null ? null : data[active];
  const focusIndex = Math.min(roving, data.length - 1);
  const centreOf = (index: number) => (index + 0.5) * slot;

  function moveFocus(next: number) {
    const clamped = Math.max(0, Math.min(data.length - 1, next));
    setRoving(clamped);
    setActive(clamped);
    columns.current[clamped]?.focus();
  }

  // The axis names the first, middle and last column, and only those that
  // exist: with a single datum the old pair of labels printed the same date at
  // both ends of the axis.
  const axisTicks = [
    { key: "first", index: 0 },
    ...(data.length > 2 ? [{ key: "mid", index: Math.floor(data.length / 2) }] : []),
    ...(data.length > 1 ? [{ key: "last", index: data.length - 1 }] : []),
  ];

  return (
    <figure className="m-0">
      <div className="relative">
        <div className="flex gap-3">
          <div
            className="num flex w-8 shrink-0 flex-col justify-between text-right text-[11px] text-muted-foreground"
            style={{ height: CHART_HEIGHT }}
            aria-hidden="true"
          >
            <span>{thousands(top)}</span>
            <span>{thousands(mid)}</span>
            <span>0</span>
          </div>

          <div className="relative flex-1" data-viz>
            <div
              className="absolute inset-x-0 top-0 border-t"
              style={{ borderColor: "var(--viz-grid)" }}
              aria-hidden="true"
            />
            {/* One outlier flattens every other column into a 2px stub, and with
                only a top and a zero tick there is nothing to read that stub
                against. The midpoint is the cheapest reference that fixes it. */}
            <div
              className="absolute inset-x-0 border-t"
              style={{ top: CHART_HEIGHT / 2, borderColor: "var(--viz-grid)" }}
              aria-hidden="true"
            />
            <div
              className="relative flex items-end"
              style={{ height: CHART_HEIGHT }}
              role="group"
              aria-label={`${valueName} by day. Use the left and right arrow keys to move between days.`}
              onMouseLeave={() => setActive(null)}
              onKeyDown={(event) => {
                const delta =
                  event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                if (delta !== 0) {
                  event.preventDefault();
                  moveFocus(focusIndex + delta);
                  return;
                }
                if (event.key === "Home") {
                  event.preventDefault();
                  moveFocus(0);
                  return;
                }
                if (event.key === "End") {
                  event.preventDefault();
                  moveFocus(data.length - 1);
                }
              }}
            >
              {data.map((point, index) => {
                const height = (point.value / top) * CHART_HEIGHT;
                return (
                  <button
                    key={point.label}
                    ref={(node) => {
                      columns.current[index] = node;
                    }}
                    type="button"
                    // The hit target is the whole slot, not the mark: the mark
                    // is capped at 24px and the slot is what the pointer aims
                    // at. The aria-label carries the value, so the column needs
                    // no aria-describedby: the one it had pointed at the full
                    // data table inside a closed <details>, which is neither in
                    // the accessibility tree nor a description of one column.
                    tabIndex={index === focusIndex ? 0 : -1}
                    className="group relative flex h-full items-end justify-center rounded-sm focus-visible:outline-2"
                    style={{ width: `${slot}%`, paddingInline: GAP / 2 }}
                    onMouseEnter={() => setActive(index)}
                    onFocus={() => {
                      setRoving(index);
                      setActive(index);
                    }}
                    onBlur={() => setActive(null)}
                    aria-label={`${point.shortLabel}: ${thousands(point.value)} ${unitName(
                      point.value,
                      valueName,
                    )}`}
                  >
                    {/* Capped rather than filling the slot: the leftover is air,
                        and the 2px surface gap does the separating. */}
                    <span
                      aria-hidden="true"
                      className="block w-full max-w-6 rounded-t-[4px] transition-opacity"
                      style={{
                        height: Math.max(point.value > 0 ? 2 : 0, height),
                        background: "var(--viz-accent)",
                        opacity: active === null || active === index ? 1 : 0.45,
                      }}
                    />
                  </button>
                );
              })}
            </div>
            {/* Labels sit at the centres of the columns they name, not in equal
                thirds: across 14 days those columns are at 3.6%, 53.6% and
                96.4%, so equal thirds pointed the outer two at nothing. */}
            <div
              className="num relative mt-2 h-6 border-t pt-1.5 text-[11px] text-muted-foreground"
              style={{ borderColor: "var(--viz-grid)" }}
              aria-hidden="true"
            >
              {axisTicks.map((tick) => (
                <span
                  key={tick.key}
                  className="absolute whitespace-nowrap"
                  style={{
                    left: `clamp(0px, calc(${centreOf(tick.index)}% - ${LABEL_EM / 2}em), calc(100% - ${LABEL_EM}em))`,
                  }}
                >
                  {data[tick.index].shortLabel}
                </span>
              ))}
            </div>
          </div>
        </div>

        <div
          className="pointer-events-none mt-3 min-h-5 text-xs text-foreground"
          role="status"
          aria-live="polite"
        >
          {activePoint ? (
            <span className="num inline-flex items-center gap-1.5 rounded-md bg-[var(--brand-soft)] px-2 py-1 text-[var(--brand-ink)]">
              <span className="font-semibold">{activePoint.shortLabel}</span>
              <span>
                <span className="font-semibold">{thousands(activePoint.value)}</span>{" "}
                {unitName(activePoint.value, valueName)}
              </span>
            </span>
          ) : null}
        </div>
      </div>

      <figcaption className="mt-1 text-xs text-muted-foreground">{caption}</figcaption>

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
          View as a table
        </summary>
        {/* Long tables get their own scroll container, never the page. */}
        <div className="mt-2 max-h-64 overflow-y-auto rounded-lg border">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-[var(--hush)]">
              <tr className="text-muted-foreground">
                <th
                  scope="col"
                  className="px-3 py-2 text-[0.6875rem] font-semibold tracking-[0.075em] uppercase"
                >
                  Date
                </th>
                <th
                  scope="col"
                  className="px-3 py-2 text-right text-[0.6875rem] font-semibold tracking-[0.075em] uppercase"
                >
                  {valueName}
                </th>
              </tr>
            </thead>
            <tbody>
              {/* The ISO date stays here: this is the machine readable view, and
                  it is the one place the exact day is worth spelling out. */}
              {data.map((point) => (
                <tr key={point.label} className="border-t">
                  <td className="num px-3 py-1.5">{point.label}</td>
                  <td className="num px-3 py-1.5 text-right font-medium">
                    {thousands(point.value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
