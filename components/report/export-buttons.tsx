"use client";

import { Check, Download, Link2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { downloadExport, type ExportKind } from "@/lib/api";
import { reportUrl } from "@/lib/format";
import type { Bundle } from "@/lib/types";

const EXPORTS: Array<{ kind: ExportKind; label: string }> = [
  { kind: "markdown", label: "Report (.md)" },
  { kind: "ideas-csv", label: "Ideas (.csv)" },
  { kind: "posts-csv", label: "Source posts (.csv)" },
  { kind: "comments-csv", label: "Source comments (.csv)" },
];

const COPIED_MS = 2200;

/** Exports always act on the bundle currently on screen, history included. */
export function ExportButtons({ bundle }: { bundle: Bundle }) {
  const [busy, setBusy] = useState<ExportKind | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<number | null>(null);

  // Only a report the server stored has somewhere to point at. A run from
  // before saved reports existed, or one whose save failed, simply keeps the
  // four export buttons it always had.
  const shareId = bundle.report_id;

  useEffect(
    () => () => {
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
    },
    [],
  );

  const run = async (kind: ExportKind) => {
    setBusy(kind);
    setFailure(null);
    try {
      await downloadExport(bundle, kind);
    } catch {
      setFailure("That export could not be built. Please try again.");
    } finally {
      setBusy(null);
    }
  };

  const copyLink = async () => {
    if (!shareId) return;
    try {
      await navigator.clipboard.writeText(reportUrl(shareId));
      setFailure(null);
      setCopied(true);
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      // Same treatment as the draft dialog's copy button: a blocked clipboard
      // is a real outcome, and a button that silently does nothing reads as
      // broken.
      setCopied(false);
      setFailure("Your browser blocked the clipboard. Copy the address bar instead.");
    }
  };

  return (
    // No `sm:items-end`: the cluster aligns to the top of the masthead with it,
    // which is what `items-start` on the row above now asks for.
    <div className="flex flex-col items-start gap-2">
      <div className="flex flex-wrap gap-2">
        {/* Five equally-weighted outline buttons outweighed the report title,
            and four of them are archival exports nobody reaches for first. Copy
            link keeps the border; the exports drop to ghost, so the cluster has
            one thing in it that looks like a button. A real menu would be the
            better answer, but the app has no menu primitive yet and adding one
            is not this pass. */}
        {shareId ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void copyLink()}
            className="h-8 gap-1.5 rounded-lg bg-card px-2.5"
          >
            {copied ? (
              <Check aria-hidden="true" strokeWidth={2} className="size-4" />
            ) : (
              <Link2 aria-hidden="true" strokeWidth={2} className="size-4" />
            )}
            {copied ? "Link copied" : "Copy link"}
          </Button>
        ) : null}
        {EXPORTS.map((item) => (
          <Button
            key={item.kind}
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy !== null}
            onClick={() => void run(item.kind)}
            className="h-8 gap-1.5 rounded-lg px-2.5"
          >
            <Download aria-hidden="true" strokeWidth={2} className="size-4" />
            {busy === item.kind ? "Preparing…" : item.label}
          </Button>
        ))}
      </div>
      {/* Announced rather than only shown: the button label changes too, but a
          screen reader user who triggered the copy from the keyboard should
          hear the outcome without hunting for it. */}
      <span aria-live="polite" className="sr-only">
        {copied ? "Link copied to your clipboard" : ""}
      </span>
      {failure ? (
        <p role="alert" className="text-xs text-destructive">
          {failure}
        </p>
      ) : null}
    </div>
  );
}
