"use client";

import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { bundleKey, runLabel } from "@/lib/format";
import type { Bundle } from "@/lib/types";

/**
 * The last eight reports from this browser, newest first.
 *
 * Kept out of the main column: it is navigation, not part of the report, and
 * on a phone it should not push the form off the screen.
 */
export function HistoryDrawer({
  open,
  history,
  activeKey,
  onSelect,
  onClose,
  onClear,
}: {
  open: boolean;
  history: Bundle[];
  activeKey: string | null;
  onSelect: (bundle: Bundle) => void;
  onClose: () => void;
  onClear: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close history"
        onClick={onClose}
        className="absolute inset-0 bg-foreground/25"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Recent reports"
        className="relative flex h-full w-full max-w-sm flex-col gap-4 overflow-y-auto border-l bg-card p-6"
      >
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            Recent reports
          </h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            Close
          </button>
        </div>

        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Reports you run appear here, on this browser only.
          </p>
        ) : (
          <>
            <ul className="flex list-none flex-col gap-2 p-0">
              {history.map((bundle) => {
                const key = bundleKey(bundle);
                const active = key === activeKey;
                return (
                  <li key={key}>
                    <button
                      type="button"
                      disabled={active}
                      onClick={() => onSelect(bundle)}
                      className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                        active
                          ? "border-[var(--brand)] bg-accent text-accent-foreground"
                          : "hover:bg-secondary"
                      }`}
                    >
                      <span className="block truncate font-medium">
                        {runLabel(bundle)}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {bundle.generated_at}
                        {active ? " · showing now" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <Button type="button" variant="ghost" size="sm" onClick={onClear}>
              Clear history
            </Button>
          </>
        )}

        <p className="mt-auto text-xs leading-relaxed text-muted-foreground">
          Stored in this browser only. Nothing is sent anywhere when you reopen a report.
        </p>
      </aside>
    </div>
  );
}
