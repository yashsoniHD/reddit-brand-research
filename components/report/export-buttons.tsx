"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { downloadExport, type ExportKind } from "@/lib/api";
import type { Bundle } from "@/lib/types";

const EXPORTS: Array<{ kind: ExportKind; label: string }> = [
  { kind: "markdown", label: "Report (.md)" },
  { kind: "ideas-csv", label: "Ideas (.csv)" },
  { kind: "posts-csv", label: "Source posts (.csv)" },
];

/** Exports always act on the bundle currently on screen, history included. */
export function ExportButtons({ bundle }: { bundle: Bundle }) {
  const [busy, setBusy] = useState<ExportKind | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

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

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {EXPORTS.map((item) => (
          <Button
            key={item.kind}
            type="button"
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => void run(item.kind)}
          >
            {busy === item.kind ? "Preparing…" : item.label}
          </Button>
        ))}
      </div>
      {failure ? (
        <p role="alert" className="text-xs text-destructive">
          {failure}
        </p>
      ) : null}
    </div>
  );
}
