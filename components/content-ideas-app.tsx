"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { Hero } from "@/components/hero";
import { HistoryDrawer } from "@/components/history-drawer";
import { HowItWorks } from "@/components/how-it-works";
import { QueryForm, type FormValues } from "@/components/query-form";
import { ReportView } from "@/components/report/report-view";
import { RunProgress } from "@/components/run-progress";
import { fetchSample, runReport } from "@/lib/api";
import { bundleKey } from "@/lib/format";
import { addToHistory, loadHistory, saveHistory } from "@/lib/storage";
import {
  MAX_PROFILE_CHARS,
  MAX_QUERY_CHARS,
  MIN_QUERY_CHARS,
  TIME_WINDOWS,
  type Bundle,
} from "@/lib/types";

const IS_DEV = process.env.NODE_ENV === "development";

const DEFAULT_VALUES: FormValues = {
  query: "",
  profile: "",
  days: 7,
};

/** Mirrors pipeline.validate_inputs so obvious mistakes never cost a round trip. */
function validate(values: FormValues): string[] {
  const errors: string[] = [];
  const query = values.query.trim();
  if (!query) {
    errors.push("Tell us what you want content ideas about.");
  } else if (query.length < MIN_QUERY_CHARS) {
    errors.push(
      `Add a little more detail about what you want content ideas about (at least ${MIN_QUERY_CHARS} characters).`,
    );
  } else if (query.length > MAX_QUERY_CHARS) {
    errors.push(`Shorten that to ${MAX_QUERY_CHARS} characters or fewer.`);
  }
  if (values.profile.trim().length > MAX_PROFILE_CHARS) {
    errors.push(
      `Shorten the description of you or your brand to ${MAX_PROFILE_CHARS} characters or fewer.`,
    );
  }
  if (!TIME_WINDOWS.includes(values.days as (typeof TIME_WINDOWS)[number])) {
    errors.push("Choose a time window of 7, 14 or 30 days.");
  }
  return errors;
}

function readQueryParams(): Partial<FormValues> {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const next: Partial<FormValues> = {};
  const query = params.get("q");
  if (query) next.query = query.slice(0, MAX_QUERY_CHARS);
  const days = Number(params.get("days"));
  if (TIME_WINDOWS.includes(days as (typeof TIME_WINDOWS)[number])) next.days = days;
  return next;
}

function writeQueryParams(bundle: Bundle): void {
  // Built fresh rather than mutated, so a stale parameter from an older link
  // cannot survive into a URL that describes a run it had no part in.
  const params = new URLSearchParams();
  params.set("q", bundle.inputs.query);
  params.set("days", String(bundle.inputs.days));
  window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
}

export function ContentIdeasApp() {
  const [values, setValues] = useState<FormValues>(DEFAULT_VALUES);
  const [report, setReport] = useState<Bundle | null>(null);
  const [history, setHistory] = useState<Bundle[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [progress, setProgress] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [sampleAvailable, setSampleAvailable] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const controller = useRef<AbortController | null>(null);
  const reportRef = useRef<HTMLDivElement>(null);

  // Seeding from the URL and from localStorage has to happen after mount: the
  // server render knows neither, and reading them during render would make the
  // markup disagree with the HTML that was sent. The one extra render this
  // costs is the point, not an accident, so the cascading-render rule is off
  // for this effect only.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setValues((current) => ({ ...current, ...readQueryParams() }));
    setHistory(loadHistory());
    const abort = new AbortController();
    void fetchSample(abort.signal).then((bundle) => setSampleAvailable(bundle !== null));
    return () => abort.abort();
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => () => controller.current?.abort(), []);

  /** Every deliberate user action retires the messages from the previous one. */
  const clearMessages = useCallback(() => setErrors([]), []);

  const show = useCallback(
    (bundle: Bundle, remember: boolean) => {
      clearMessages();
      setReport(bundle);
      if (remember) {
        setHistory((current) => {
          const next = addToHistory(current, bundle);
          saveHistory(next);
          return next;
        });
      }
    },
    [clearMessages],
  );

  const onChange = useCallback(
    (next: FormValues) => {
      if (errors.length > 0) clearMessages();
      setValues(next);
    },
    [errors.length, clearMessages],
  );

  const onSubmit = useCallback(async () => {
    clearMessages();
    const problems = validate(values);
    if (problems.length > 0) {
      setErrors(problems);
      return;
    }

    const abort = new AbortController();
    controller.current = abort;
    setRunning(true);
    setProgress(["Starting…"]);

    const outcome = await runReport(
      {
        query: values.query.trim(),
        profile: values.profile.trim(),
        days: values.days,
      },
      {
        signal: abort.signal,
        onProgress: (message) => setProgress((current) => [...current, message]),
      },
    );

    controller.current = null;
    setRunning(false);
    setProgress([]);

    if (outcome.kind === "aborted") return;
    if (outcome.kind === "report") {
      show(outcome.bundle, true);
      writeQueryParams(outcome.bundle);
      window.requestAnimationFrame(() =>
        reportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
      return;
    }
    if (outcome.kind === "invalid") {
      setErrors(
        outcome.errors.length > 0
          ? outcome.errors
          : ["That search could not be used. Please adjust it and try again."],
      );
      return;
    }
    // A failed run must never destroy the report already on screen.
    setErrors([
      report
        ? `${outcome.message} Your previous report is still shown below.`
        : outcome.message,
    ]);
  }, [values, clearMessages, show, report]);

  const onCancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setRunning(false);
    setProgress([]);
    clearMessages();
  }, [clearMessages]);

  const onLoadSample = useCallback(async () => {
    clearMessages();
    const bundle = await fetchSample();
    if (!bundle) {
      setErrors(["The sample report is not available right now. Please try a search."]);
      setSampleAvailable(false);
      return;
    }
    show(bundle, false);
  }, [clearMessages, show]);

  const onLoadDemo = useCallback(async () => {
    clearMessages();
    const { MOCK_BUNDLE } = await import("@/lib/mock-bundle");
    show(MOCK_BUNDLE, false);
  }, [clearMessages, show]);

  const activeKey = report ? bundleKey(report) : null;

  return (
    <>
      <header className="sticky top-0 z-30 border-b bg-background/85 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3 sm:px-8">
          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo.svg" alt="" width={20} height={20} className="size-5" />
            Reddit Content Ideas
          </span>
          <button
            type="button"
            onClick={() => {
              clearMessages();
              setHistoryOpen(true);
            }}
            className="rounded-lg px-3 py-1.5 text-sm font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            Recent reports{history.length > 0 ? ` (${history.length})` : ""}
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-col gap-16 px-5 py-14 sm:px-8 sm:py-20">
        <div className="flex flex-col gap-10">
          <Hero />

          <div className="flex flex-col gap-4">
            <ErrorBanner messages={errors} onDismiss={clearMessages} />
            <QueryForm
              values={values}
              onChange={onChange}
              onSubmit={() => void onSubmit()}
              onCancel={onCancel}
              running={running}
            />
            {running ? <RunProgress messages={progress} /> : null}
          </div>
        </div>

        <HowItWorks />

        <div className="flex flex-col gap-3">
          {sampleAvailable ? (
            <div>
              <button
                type="button"
                onClick={() => void onLoadSample()}
                className="rounded-lg border px-4 py-2 text-sm font-medium hover:bg-secondary"
              >
                See a sample report
              </button>
              <p className="mt-2 text-xs text-muted-foreground">
                A real run over real Reddit data, so you can see the output before
                spending a minute on your own.
              </p>
            </div>
          ) : null}

          {IS_DEV ? (
            <button
              type="button"
              onClick={() => void onLoadDemo()}
              className="self-start text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              Load demo report (development only)
            </button>
          ) : null}

          <p className="text-xs leading-relaxed text-muted-foreground">
            Analyses public Reddit posts and comments via the Arctic Shift archive. No
            Reddit login needed. Your search is never stored.
          </p>
        </div>

        <div ref={reportRef}>
          {report ? (
            <ReportView bundle={report} />
          ) : (
            <section className="surface-hush border-dashed px-6 py-14 text-center">
              <p className="text-sm font-medium text-foreground">
                Your ideas will appear here.
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
                Eight to twelve ideas with the opening line already written, each one
                traceable to the posts it came from, and each one a click away from a
                finished post.
              </p>
            </section>
          )}
        </div>
      </main>

      <footer className="border-t">
        <div className="mx-auto max-w-6xl px-5 py-6 text-xs leading-relaxed text-muted-foreground sm:px-8">
          Reddit Content Ideas. Public Reddit data via the Arctic Shift archive. Not
          affiliated with Reddit.
        </div>
      </footer>

      <HistoryDrawer
        open={historyOpen}
        history={history}
        activeKey={activeKey}
        onSelect={(bundle) => {
          show(bundle, false);
          setHistoryOpen(false);
        }}
        onClose={() => setHistoryOpen(false)}
        onClear={() => {
          setHistory([]);
          saveHistory([]);
          clearMessages();
        }}
      />
    </>
  );
}
