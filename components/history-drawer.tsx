"use client";

import { Check, Link2, X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { bundleKey, reportUrl, runLabel } from "@/lib/format";
import type { Bundle } from "@/lib/types";

const COPIED_MS = 2200;

/**
 * Copy the link to one stored report.
 *
 * Sits beside the entry rather than inside its button, because a button inside
 * a button is not a valid target, and because opening a report and sharing it
 * are two different intentions.
 */
function CopyLinkButton({ reportId, label }: { reportId: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(reportUrl(reportId));
      setCopied(true);
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      // Nothing useful to say inside a list row: the report itself is one
      // click away, and its own Copy link button reports the same failure
      // where there is room for a sentence.
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={copied ? "Link copied" : "Copy link to this report"}
      className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-[var(--hush)] hover:text-foreground"
    >
      {copied ? (
        <Check aria-hidden="true" strokeWidth={2.5} className="size-3.5" />
      ) : (
        <Link2 aria-hidden="true" strokeWidth={2} className="size-3.5" />
      )}
      <span className="sr-only">
        {copied ? "Link copied" : `Copy link to ${label}`}
      </span>
    </button>
  );
}

/**
 * The last eight reports from this browser, newest first.
 *
 * Kept out of the main column: it is navigation, not part of the report, and
 * on a phone it should not push the form off the screen.
 *
 * Built on the same Radix Dialog the draft dialog uses, rather than on a div
 * that claimed `aria-modal` and left the page behind it fully tabbable. The
 * primitive is what actually delivers the promise: focus moves in, stays in
 * while the panel is open, returns to the trigger on close, the background is
 * inert to assistive technology, and Escape closes.
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
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-foreground/25 supports-backdrop-filter:backdrop-blur-xs data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          // This version of Radix marks the rest of the page aria-hidden but
          // does not put aria-modal on the panel itself, and a screen reader
          // that keys on aria-modal rather than on the hidden siblings would
          // announce this as an ordinary region. Stated explicitly, so both
          // routes to the same promise agree.
          aria-modal="true"
          className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-sm flex-col gap-5 overflow-y-auto border-l bg-card p-6 shadow-[var(--lift-3)] outline-none data-open:animate-in data-open:slide-in-from-right data-closed:animate-out data-closed:slide-out-to-right"
        >
          <div className="flex items-center justify-between gap-4 border-b pb-4">
            <DialogPrimitive.Title className="h-quiet">
              Recent reports
            </DialogPrimitive.Title>
            <DialogPrimitive.Close className="-mr-2 flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
              <X aria-hidden="true" strokeWidth={2} className="size-4" />
              <span className="sr-only">Close</span>
            </DialogPrimitive.Close>
          </div>

          {history.length === 0 ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              Reports you run appear here, on this browser only.
            </p>
          ) : (
            <>
              <ul className="flex list-none flex-col gap-2 p-0">
                {history.map((bundle) => {
                  const key = bundleKey(bundle);
                  const active = key === activeKey;
                  const label = runLabel(bundle);
                  return (
                    <li key={key} className="flex items-center gap-1">
                      <button
                        type="button"
                        disabled={active}
                        onClick={() => onSelect(bundle)}
                        className={`min-w-0 flex-1 rounded-xl border px-3.5 py-3 text-left text-sm transition-colors ${
                          active
                            ? "border-[var(--brand-line)] bg-[var(--brand-soft)] text-foreground"
                            : "hover:border-[var(--brand-line)] hover:bg-[var(--hush)]"
                        }`}
                      >
                        <span className="block truncate font-medium">{label}</span>
                        <span className="num mt-1 block text-xs text-muted-foreground">
                          {bundle.generated_at}
                          {active ? " · showing now" : ""}
                        </span>
                      </button>
                      {/* Only reports the server stored have a link to give.
                          An older entry keeps the row it always had. */}
                      {bundle.report_id ? (
                        <CopyLinkButton reportId={bundle.report_id} label={label} />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClear}
                className="self-start"
              >
                Clear history
              </Button>
            </>
          )}

          <p className="mt-auto border-t pt-4 text-xs leading-relaxed text-muted-foreground">
            This list lives in this browser only, and reopening a report from it sends
            nothing anywhere. Reports with a link are also saved on the server, so that
            link keeps working on another device.
          </p>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
