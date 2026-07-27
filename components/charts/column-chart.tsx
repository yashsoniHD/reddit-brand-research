"use client";

import { useId, useState } from "react";

/**
 * Daily counts as columns: one series, one baseline, one y scale.
 *
 * Values are not labelled on every column (that reads as noise across a
 * 30 day window), so the hover tooltip and the accessible table below carry
 * the numbers the axis does not.
 */

export interface ColumnDatum {
  label: string;
  shortLabel: string;
  value: number;
}

const CHART_HEIGHT = 168;
const GAP = 2;

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
  const tableId = useId();

  if (data.length === 0) return null;

  const max = Math.max(...data.map((point) => point.value), 1);
  // Round the top tick to something a person would write down.
  const step = max <= 5 ? 1 : max <= 20 ? 5 : max <= 60 ? 10 : 25;
  const top = Math.ceil(max / step) * step;
  const slot = 100 / data.length;
  const activePoint = active === null ? null : data[active];

  return (
    <figure className="m-0">
      <div className="relative">
        <div className="flex gap-3">
          <div
            className="num flex w-8 shrink-0 flex-col justify-between text-right text-[11px] text-muted-foreground"
            style={{ height: CHART_HEIGHT }}
            aria-hidden="true"
          >
            <span>{top}</span>
            <span>0</span>
          </div>

          <div className="relative flex-1">
            <div
              className="absolute inset-x-0 top-0 border-t"
              style={{ borderColor: "var(--viz-grid)" }}
              aria-hidden="true"
            />
            <div
              className="relative flex items-end"
              style={{ height: CHART_HEIGHT }}
              onMouseLeave={() => setActive(null)}
            >
              {data.map((point, index) => {
                const height = (point.value / top) * CHART_HEIGHT;
                return (
                  <button
                    key={point.label}
                    type="button"
                    className="group relative flex h-full items-end justify-center rounded-sm focus-visible:outline-2"
                    style={{ width: `${slot}%`, paddingInline: GAP / 2 }}
                    onMouseEnter={() => setActive(index)}
                    onFocus={() => setActive(index)}
                    onBlur={() => setActive(null)}
                    aria-describedby={tableId}
                    aria-label={`${point.label}: ${point.value} ${valueName}`}
                  >
                    {/* Capped rather than filling the slot: the leftover is air,
                        and the 2px surface gap does the separating. */}
                    <span
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
            <div
              className="num mt-2 flex border-t pt-1.5 text-[11px] text-muted-foreground"
              style={{ borderColor: "var(--viz-grid)" }}
              aria-hidden="true"
            >
              <span className="flex-1 text-left">{data[0].shortLabel}</span>
              {data.length > 2 ? (
                <span className="flex-1 text-center">
                  {data[Math.floor(data.length / 2)].shortLabel}
                </span>
              ) : null}
              <span className="flex-1 text-right">{data[data.length - 1].shortLabel}</span>
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
              <span className="font-semibold">{activePoint.label}</span>
              <span className="font-semibold">{activePoint.value}</span> {valueName}
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
          <table id={tableId} className="w-full text-left text-xs">
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
              {data.map((point) => (
                <tr key={point.label} className="border-t">
                  <td className="num px-3 py-1.5">{point.label}</td>
                  <td className="num px-3 py-1.5 text-right font-medium">
                    {point.value}
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
