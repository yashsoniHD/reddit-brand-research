"use client";

import { FileText } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { ReportView } from "@/components/report/report-view";
import { fetchReport } from "@/lib/api";
import type { Bundle } from "@/lib/types";

/**
 * A report opened from its link.
 *
 * The fetch is client-side on purpose. The bundle is megabytes of posts and
 * comments, and rendering it on the server would mean serialising all of it
 * into the RSC payload and shipping it again inside the HTML, so the page would
 * pay for the report twice before the reader saw a word of it. Fetching it here
 * also keeps this route on exactly the same code path as a live run: one
 * `ReportView` over one bundle, nothing forked.
 */

type State =
  | { phase: "loading" }
  | { phase: "ready"; bundle: Bundle; drafts: Record<string, string> }
  | { phase: "missing"; message: string }
  | { phase: "error"; message: string };

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <AppShell
      // This page is not home, so the wordmark links back to it.
      linkHome
      action={
        <Link
          href="/"
          className="flex h-8 items-center rounded-lg border border-transparent px-3 text-sm font-medium text-muted-foreground transition-colors hover:border-border hover:bg-card hover:text-foreground"
        >
          Run your own
        </Link>
      }
    >
      {/* AppShell's <main> is a plain block: the rhythm inside the column is
          this page's to set, and it is not the landing page's. */}
      <div className="flex flex-col gap-10">{children}</div>
    </AppShell>
  );
}

/** The shape of the report, drawn while the real one is on its way. */
function Skeleton() {
  return (
    <div className="flex flex-col gap-8" aria-busy="true">
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <span aria-hidden="true" className="relative flex size-2">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--brand)] opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-[var(--brand)]" />
        </span>
        Loading this report…
      </p>
      <div className="flex flex-col gap-3 border-b pb-6">
        <span className="block h-6 w-2/3 animate-pulse rounded bg-muted sm:w-1/3" />
        <span className="block h-3 w-1/2 animate-pulse rounded bg-muted sm:w-1/4" />
      </div>
      {/* The same grid the ideas land in, at the height a real IdeaCard settles
          at (hook, title, angle, the draft row and the source rail). It used to
          be `rounded-2xl bg-muted/40` on top of `.surface`, overriding two of
          the three things `.surface` is for at a radius and a height the real
          card never has, so the swap-in visibly jumped. */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <span key={index} className="surface block h-72 animate-pulse" />
        ))}
      </div>
    </div>
  );
}

function Empty({ title, message }: { title: string; message: string }) {
  return (
    <section className="surface-hush flex flex-col items-center gap-3 border-dashed px-6 py-16 text-center">
      <FileText
        aria-hidden="true"
        strokeWidth={2}
        className="size-6 text-muted-foreground"
      />
      <p className="h-zone text-foreground">{title}</p>
      <p className="mx-auto max-w-md text-sm leading-relaxed text-muted-foreground">
        {message}
      </p>
      <Link
        href="/"
        className="mt-2 flex h-10 items-center rounded-xl border bg-card px-4 text-sm font-medium transition-colors hover:border-[var(--brand-line)] hover:bg-[var(--brand-soft)] hover:text-[var(--brand-ink)]"
      >
        Run a new search
      </Link>
    </section>
  );
}

export function SavedReport({ id }: { id: string }) {
  const [state, setState] = useState<State>({ phase: "loading" });

  // The fetch is the whole page, so it belongs in an effect keyed on the id.
  // Every setState here happens after the request resolves, which is the
  // subscribe-to-an-external-system case rather than a render-time cascade.
  useEffect(() => {
    const abort = new AbortController();
    void fetchReport(id, abort.signal).then((outcome) => {
      if (abort.signal.aborted) return;
      if (outcome.kind === "report") {
        setState({ phase: "ready", bundle: outcome.bundle, drafts: outcome.drafts });
        return;
      }
      setState({ phase: outcome.kind, message: outcome.message });
    });
    return () => abort.abort();
  }, [id]);

  if (state.phase === "loading") {
    return (
      <Shell>
        <Skeleton />
      </Shell>
    );
  }

  if (state.phase === "missing") {
    return (
      <Shell>
        <Empty title="Report not found" message={state.message} />
      </Shell>
    );
  }

  if (state.phase === "error") {
    return (
      <Shell>
        <Empty title="That report could not be loaded" message={state.message} />
      </Shell>
    );
  }

  return (
    <Shell>
      <ReportView bundle={state.bundle} initialDrafts={state.drafts} />
    </Shell>
  );
}
