"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect } from "react";

/**
 * The last line of defence.
 *
 * Every render path below this is guarded, but a bundle is a large object
 * assembled by another process, and one unexpected shape should cost the user a
 * button press rather than a blank page. Reloading is offered alongside the
 * in-place reset because a corrupt report in memory survives a reset and does
 * not survive a reload.
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
    <main className="mx-auto flex w-full max-w-xl flex-col items-start gap-6 px-5 py-24 sm:px-8">
      <span className="flex size-11 items-center justify-center rounded-xl border border-destructive/30 bg-destructive/10">
        <AlertTriangle
          aria-hidden="true"
          strokeWidth={2}
          className="size-5 text-destructive"
        />
      </span>

      <div className="flex flex-col gap-3">
        <h1 className="t-title text-foreground">Something went wrong on this page.</h1>
        <p className="text-[0.9375rem] leading-relaxed text-foreground-soft">
          The report could not be displayed. Your search was not lost: try again, and if
          it keeps happening, reload the page to start clean.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={reset}
          className="h-10 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="h-10 rounded-xl border bg-card px-4 text-sm font-medium transition-colors hover:bg-secondary"
        >
          Reload the page
        </button>
      </div>

      {error.digest ? (
        <p className="text-xs text-muted-foreground">
          Reference:{" "}
          <span className="num rounded bg-muted px-1.5 py-0.5 font-mono">
            {error.digest}
          </span>
        </p>
      ) : null}
    </main>
  );
}
