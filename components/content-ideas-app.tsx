"use client";

import { FileText } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { Hero } from "@/components/hero";
import { HistoryDrawer } from "@/components/history-drawer";
import { QueryForm, type FormValues } from "@/components/query-form";
import { ReportView } from "@/components/report/report-view";
import { RunProgress } from "@/components/run-progress";
import { fetchSample, fetchSampleAvailable, runReport } from "@/lib/api";
import { bundleKey, reportPath } from "@/lib/format";
import { addToHistory, loadHistory, saveHistory } from "@/lib/storage";
import { checkCommunities, mentionsCommunities } from "@/lib/validate";
import {
  DEFAULT_DAYS,
  MAX_PROFILE_CHARS,
  MAX_QUERY_CHARS,
  MIN_QUERY_CHARS,
  TIME_WINDOWS,
  bundleContentTypes,
  parseContentTypes,
  type Bundle,
  type ContentType,
} from "@/lib/types";

const IS_DEV = process.env.NODE_ENV === "development";

const DEFAULT_VALUES: FormValues = {
  query: "",
  profile: "",
  subreddits: "",
  days: DEFAULT_DAYS,
  relevance: true,
  contentTypes: [],
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
  errors.push(...checkCommunities(values.subreddits).messages);
  return errors;
}

/**
 * What a URL says about a run.
 *
 * This is the settings-only form: it reproduces the search, not the result,
 * and it is what a run that could not be saved falls back to. A saved run gets
 * the better URL instead (`/r/<id>`, written by `writeSavedReportPath` below),
 * which reloads the report itself.
 */
interface UrlState {
  query: string;
  days?: number;
  /** Only ever the communities the user chose by hand. */
  subreddits?: string;
  relevance?: boolean;
  contentTypes?: ContentType[];
}

function readQueryParams(): Partial<FormValues> {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const next: Partial<FormValues> = {};
  const query = params.get("q");
  if (query) next.query = query.slice(0, MAX_QUERY_CHARS);
  const days = Number(params.get("days"));
  if (TIME_WINDOWS.includes(days as (typeof TIME_WINDOWS)[number])) next.days = days;
  const subs = params.get("subs");
  if (subs) next.subreddits = subs.slice(0, 120);
  const relevance = params.get("rel");
  if (relevance === "0") next.relevance = false;
  if (relevance === "1") next.relevance = true;
  // `type` is comma-separated now. A link written when it held one bare name
  // still parses, because splitting a single value yields that single value.
  const contentTypes = parseContentTypes(params.get("type"));
  if (contentTypes.length > 0) next.contentTypes = contentTypes;
  return next;
}

function writeQueryParams(state: UrlState): void {
  if (typeof window === "undefined") return;
  // Built fresh rather than mutated, so a stale parameter from an older link
  // cannot survive into a URL that describes a run it had no part in.
  const params = new URLSearchParams();
  params.set("q", state.query);
  // Advanced settings appear only when they differ from the defaults, so a
  // default run keeps the short shareable URL it always had.
  if (state.days && state.days !== DEFAULT_DAYS) params.set("days", String(state.days));
  if (state.subreddits) params.set("subs", state.subreddits);
  if (state.relevance === false) params.set("rel", "0");
  if (state.contentTypes && state.contentTypes.length > 0) {
    params.set("type", state.contentTypes.join(","));
  }
  // Rooted at "/" rather than at the current path: once a saved run has
  // rewritten the URL to /r/<id>, a later unsaved run must not produce
  // "/r/<old-id>?q=..." , which would be a link to somebody else's report
  // carrying this one's search.
  window.history.replaceState(null, "", `/?${params}`);
}

/**
 * Point the address bar at the saved report without navigating.
 *
 * replaceState rather than a router push: the report is already rendered and
 * the page holds state (the draft cache, the history list, the form) that a
 * navigation would throw away and rebuild for no gain. What this buys is a URL
 * worth copying and a reload that comes back to the same report.
 */
function writeSavedReportPath(reportId: string): void {
  if (typeof window === "undefined") return;
  window.history.replaceState(null, "", reportPath(reportId));
}

/**
 * The URL state for a bundle.
 *
 * `form` is passed for a run that just finished, because the form is the only
 * record of what was asked for: the bundle reports the communities that
 * contributed data, not whether the user picked them, and it carries no trace
 * of the relevance switch at all. Without it (a history entry, the sample) the
 * bundle answers what it can and the rest is left off the URL.
 */
function urlStateFor(bundle: Bundle, form?: FormValues): UrlState {
  const inputs = bundle.inputs ?? { query: "", profile: "", subreddits: [] };
  if (form) {
    return {
      query: inputs.query || form.query,
      days: inputs.days ?? form.days,
      subreddits: form.subreddits.trim() || undefined,
      relevance: form.relevance,
      contentTypes: form.contentTypes,
    };
  }
  const manual = inputs.discovery === "manual";
  return {
    query: inputs.query,
    days: inputs.days,
    subreddits: manual ? (inputs.subreddits ?? []).join(",") || undefined : undefined,
    contentTypes: bundleContentTypes(inputs),
  };
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
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [highlightCommunities, setHighlightCommunities] = useState(false);

  const controller = useRef<AbortController | null>(null);
  const reportRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);

  // Seeding from the URL and from localStorage has to happen after mount: the
  // server render knows neither, and reading them during render would make the
  // markup disagree with the HTML that was sent. The one extra render this
  // costs is the point, not an accident, so the cascading-render rule is off
  // for this effect only.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const seeded = readQueryParams();
    setValues((current) => ({ ...current, ...seeded }));
    // A link that carries advanced settings should show them, not hide the
    // reason the form looks different from its defaults.
    if (
      seeded.subreddits !== undefined ||
      seeded.relevance !== undefined ||
      seeded.contentTypes !== undefined ||
      (seeded.days !== undefined && seeded.days !== DEFAULT_DAYS)
    ) {
      setAdvancedOpen(true);
    }
    setHistory(loadHistory());
    const abort = new AbortController();
    // A yes/no call, not the sample itself: the bundle is megabytes, and on
    // first paint all the page needs to know is whether to offer the button.
    void fetchSampleAvailable(abort.signal).then(setSampleAvailable);
    return () => abort.abort();
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => () => controller.current?.abort(), []);

  const scrollTo = useCallback((element: HTMLElement | null) => {
    if (!element) return;
    window.requestAnimationFrame(() =>
      element.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }, []);

  /**
   * Take the reader to the run that just started.
   *
   * This has to be an effect rather than a line in the submit handler: at the
   * moment the handler flips `running` the progress panel has not been
   * rendered yet, so the ref still holds null and the scroll would silently do
   * nothing. By the time an effect keyed on `running` runs, the panel is in the
   * document and the ref points at it.
   */
  useEffect(() => {
    if (!running) return;
    scrollTo(progressRef.current);
  }, [running, scrollTo]);

  /** Every deliberate user action retires the messages from the previous one. */
  const clearMessages = useCallback(() => {
    setErrors([]);
    setHighlightCommunities(false);
  }, []);

  /**
   * Report a failure and take the user to it.
   *
   * A banner that renders above the fold the user is not looking at is a banner
   * nobody reads, and when the complaint is about a field inside a collapsed
   * panel, the panel has to open or the message points at nothing.
   */
  const fail = useCallback(
    (messages: string[]) => {
      setErrors(messages);
      const aboutCommunities = messages.some(mentionsCommunities);
      if (aboutCommunities) {
        setAdvancedOpen(true);
        setHighlightCommunities(true);
      }
      scrollTo(errorRef.current);
    },
    [scrollTo],
  );

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
      fail(problems);
      return;
    }

    const abort = new AbortController();
    controller.current = abort;
    setRunning(true);
    setProgress(["Starting…"]);
    // The scroll to the progress panel lives in the effect above, because the
    // panel does not exist yet at this point in the handler.

    const outcome = await runReport(
      {
        query: values.query.trim(),
        profile: values.profile.trim(),
        days: values.days,
        ...(values.subreddits.trim() ? { subreddits: values.subreddits.trim() } : {}),
        relevance_filter: values.relevance,
        ...(values.contentTypes.length > 0
          ? { content_types: values.contentTypes }
          : {}),
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
      // A saved run gets the link to the report itself; one the server could
      // not store keeps the settings URL, which is all it can honestly offer.
      if (outcome.bundle.report_id) {
        writeSavedReportPath(outcome.bundle.report_id);
      } else {
        writeQueryParams(urlStateFor(outcome.bundle, values));
      }
      scrollTo(reportRef.current);
      return;
    }
    if (outcome.kind === "invalid") {
      fail(
        outcome.errors.length > 0
          ? outcome.errors
          : ["That search could not be used. Please adjust it and try again."],
      );
      return;
    }
    // A failed run must never destroy the report already on screen.
    fail([
      report
        ? `${outcome.message} Your previous report is still shown below.`
        : outcome.message,
    ]);
  }, [values, clearMessages, fail, scrollTo, show, report]);

  const onCancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setRunning(false);
    setProgress([]);
    clearMessages();
    // Losing the progress panel makes the document shorter, and on a page that
    // was scrolled near its old bottom the browser paints one frame with the
    // sticky header floating over blank space before it settles. React has
    // already flushed the removal by the time this frame runs, so clamping the
    // offset to the shorter document removes that frame.
    window.requestAnimationFrame(() => {
      const bottom = Math.max(
        0,
        document.documentElement.scrollHeight - window.innerHeight,
      );
      if (window.scrollY > bottom) window.scrollTo({ top: bottom });
    });
  }, [clearMessages]);

  const onLoadSample = useCallback(async () => {
    clearMessages();
    const bundle = await fetchSample();
    if (!bundle) {
      fail(["The sample report is not available right now. Please try a search."]);
      setSampleAvailable(false);
      return;
    }
    show(bundle, false);
    writeQueryParams(urlStateFor(bundle));
    scrollTo(reportRef.current);
  }, [clearMessages, fail, scrollTo, show]);

  const onLoadDemo = useCallback(async () => {
    clearMessages();
    try {
      const { MOCK_BUNDLE } = await import("@/lib/mock-bundle");
      show(MOCK_BUNDLE, false);
    } catch {
      // A failed chunk load is a dev-only annoyance, but an unhandled rejection
      // is invisible, and an invisible dead button is worse than a sentence.
      fail(["The demo report could not be loaded."]);
    }
  }, [clearMessages, fail, show]);

  const activeKey = report ? bundleKey(report) : null;

  return (
    <>
      <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3 sm:px-8">
          <span className="flex items-center gap-2.5 text-sm font-semibold tracking-[-0.012em] text-foreground">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo.svg" alt="" width={22} height={22} className="size-[22px]" />
            Reddit Content Ideas
          </span>
          <button
            type="button"
            onClick={() => {
              clearMessages();
              setHistoryOpen(true);
            }}
            className="flex h-8 items-center gap-2 rounded-lg border border-transparent px-3 text-sm font-medium text-muted-foreground transition-colors hover:border-border hover:bg-card hover:text-foreground"
          >
            Recent reports
            {history.length > 0 ? (
              <span className="num inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--brand-soft)] px-1.5 text-[0.6875rem] font-semibold text-[var(--brand-ink)]">
                {history.length}
              </span>
            ) : null}
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-col gap-16 px-5 py-12 sm:px-8 sm:py-20">
        <div className="flex flex-col gap-10">
          <Hero />

          {/* The search zone stays a narrow column even on a wide screen: a
              three-row textarea stretched to 1100px is a worse target than a
              centred one, and the report below gets the full width instead. */}
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
            <div ref={errorRef} className="scroll-mt-20">
              <ErrorBanner messages={errors} onDismiss={clearMessages} />
            </div>
            <QueryForm
              values={values}
              onChange={onChange}
              onSubmit={() => void onSubmit()}
              onCancel={onCancel}
              running={running}
              advancedOpen={advancedOpen}
              onAdvancedOpenChange={setAdvancedOpen}
              highlightCommunities={highlightCommunities}
            />
            <div ref={progressRef} className="scroll-mt-20">
              {running ? <RunProgress messages={progress} /> : null}
            </div>

            {/* The one thing offered besides the form, so it sits with the
                form rather than in a section of its own. */}
            {!report && sampleAvailable ? (
              <button
                type="button"
                onClick={() => void onLoadSample()}
                // A run in flight owns the page. Swapping the sample in
                // underneath it would replace the reader's report the
                // moment their own finished.
                disabled={running}
                className="self-center text-sm font-medium text-foreground-soft underline underline-offset-4 transition-colors hover:text-[var(--brand-ink)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:text-foreground-soft"
              >
                See a sample report
              </button>
            ) : null}

            {!report && IS_DEV ? (
              <button
                type="button"
                onClick={() => void onLoadDemo()}
                disabled={running}
                className="self-center text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
              >
                Load demo report (development only)
              </button>
            ) : null}
          </div>
        </div>

        <div ref={reportRef} className="scroll-mt-20">
          {report ? (
            <ReportView bundle={report} />
          ) : (
            <section className="surface-hush flex flex-col items-center gap-3 border-dashed px-6 py-12 text-center">
              <FileText
                aria-hidden="true"
                strokeWidth={1.5}
                className="size-6 text-muted-foreground"
              />
              <p className="h-zone text-foreground">Your ideas will appear here.</p>
            </section>
          )}
        </div>
      </main>

      <footer className="mt-auto border-t">
        <div className="mx-auto flex max-w-6xl flex-col gap-1 px-5 py-8 text-xs leading-relaxed text-muted-foreground sm:px-8">
          <span className="font-medium text-foreground-soft">Reddit Content Ideas</span>
          <span>
            Public Reddit data via the Arctic Shift archive. Not affiliated with Reddit.
          </span>
        </div>
      </footer>

      <HistoryDrawer
        open={historyOpen}
        history={history}
        activeKey={activeKey}
        onSelect={(bundle) => {
          show(bundle, false);
          if (bundle.report_id) {
            writeSavedReportPath(bundle.report_id);
          } else {
            writeQueryParams(urlStateFor(bundle));
          }
          setHistoryOpen(false);
          scrollTo(reportRef.current);
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
