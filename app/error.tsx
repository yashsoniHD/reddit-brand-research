"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";

/**
 * The last line of defence.
 *
 * Every render path below this is guarded, but a bundle is a large object
 * assembled by another process, and one unexpected shape should cost the user a
 * button press rather than a blank page. Reloading is offered alongside the
 * in-place reset because a corrupt report in memory survives a reset and does
 * not survive a reload.
 *
 * Wrapped in the same AppShell as every other page: a crash is the worst
 * moment for the chrome to suddenly look like a different product. AppShell's
 * <main> carries no flex/gap of its own, so the column layout below is ours.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // The digest is the only handle on the server-side trace, so it goes to the
    // console where a bug report can pick it up.
    console.error("Reddit Content Ideas crashed:", error);
  }, [error]);

  return (
    <AppShell linkHome>
      <div className="mx-auto flex w-full max-w-xl flex-col items-start gap-6">
        <span className="flex size-11 items-center justify-center rounded-xl border border-destructive/30 bg-destructive/10">
          <AlertTriangle
            aria-hidden="true"
            strokeWidth={2}
            className="size-5 text-destructive"
          />
        </span>

        <div className="flex flex-col gap-3">
          <h1 className="t-title text-foreground">This report could not be shown.</h1>
          <p className="t-body text-foreground-soft">
            Your search was not lost: try again, and if it keeps happening, reload the
            page to start clean.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" onClick={reset}>
            Try again
          </Button>
          <Button type="button" variant="outline" onClick={() => window.location.reload()}>
            Reload the page
          </Button>
        </div>

        {error.digest ? (
          <p className="text-xs text-muted-foreground">
            Reference:{" "}
            <span className="num rounded bg-muted px-1.5 py-0.5 font-mono">
              {error.digest}
            </span>
          </p>
        ) : null}
      </div>
    </AppShell>
  );
}
