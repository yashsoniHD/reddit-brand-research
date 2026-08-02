"use client";

import { Check } from "lucide-react";
import { useEffect, useRef } from "react";

/**
 * Staged progress, fed by the real NDJSON progress lines.
 *
 * The engine emits free text, so each line is matched to one of five stages and
 * shown verbatim underneath it. The raw log stays visible: when a run is slow,
 * "Fetching posts from r/x (240 so far)" is the difference between "it is
 * working" and "it is stuck".
 *
 * The stage a run is at is the furthest stage any line has reached, never the
 * stage of the newest line. The engine interleaves messages (a late "Fetching
 * comments…" arrives after selection has been announced, and an unmatched line
 * such as "Starting…" scores zero), so reading only the last line made the
 * stepper walk backwards mid-run.
 *
 * Every pattern below is matched against a real `say(...)` call in
 * pipeline.py's `run_report`. Retrieval has two modes, scrape and search, and
 * both have to land on the "read" stage: a search-mode run emits "Searching
 * r/x for y…", "Reading the replies to the n strongest threads…", and the two
 * "Too little matched/mentioning…" widen-and-retry lines instead of
 * "Fetching…", and none of those matched here before, so a search-mode run
 * (the default path) sat on "Finding communities" for the whole 76-200s
 * retrieval phase.
 */

const STAGES = [
  {
    id: "discover",
    label: "Finding communities",
    // "Finding communities talking about this…", "Found r/a, r/b… Fetching
    // posts…", "Checking the communities you chose…" (the manual-subreddits
    // path, which skips discovery's model call but still announces itself).
    match: /^(finding communities|found |checking the communities)/i,
  },
  {
    id: "read",
    label: "Reading posts and comments",
    // Scrape mode: "Fetching posts from r/x…", "Fetching posts from r/x…
    // (n so far)", "Fetching comments from r/x…". Search mode: "Searching
    // r/x for y…", "Searching r/x for y… (n found)", "Reading the replies to
    // the n strongest threads…", and the two retry lines emitted when a
    // search or a phrase filter came back too thin: "Too little matched
    // those searches. Reading those communities…" and "Too little
    // mentioning {phrase}. Reading those communities again…".
    match: /^(fetching |searching r\/|reading the replies|too little (matched|mentioning))/i,
  },
  {
    id: "select",
    label: "Selecting the sample",
    // "Selecting the most relevant items…" or "Selecting items…" depending
    // on whether the relevance filter is on.
    match: /^selecting /i,
  },
  {
    id: "classify",
    label: "Classifying the sample",
    // "Classifying the sample…" then "Classified n posts and m comments…".
    match: /^classif/i,
  },
  {
    id: "write",
    label: "Writing your content ideas",
    match: /^writing /i,
  },
] as const;

function stageIndexFor(message: string): number {
  for (let index = STAGES.length - 1; index >= 0; index -= 1) {
    if (STAGES[index].match.test(message)) return index;
  }
  return 0;
}

/** The furthest stage reached. Monotonic by construction: messages only append. */
function highestStage(messages: string[]): number {
  return messages.reduce((highest, message) => {
    const index = stageIndexFor(message);
    return index > highest ? index : highest;
  }, 0);
}

/**
 * The community names already announced in the log, read back out of it.
 *
 * Discovery emits one line of the shape "Found r/a, r/b, r/c. Fetching posts…".
 * That line is the single most convincing thing that happens during the wait,
 * and buried in a one-line tail it is easy to miss, so it is lifted out and
 * shown as chips. Nothing new is requested or computed: this is the same string
 * the stepper is already displaying, read a second way.
 */
const FOUND = /^found\s+(r\/[\w-]+(?:\s*,\s*r\/[\w-]+)*)/i;

/* There used to be an elapsed clock beside the step count here, on the
   argument that a search-mode run can sit on the same heartbeat line for ten
   seconds and needed something visibly moving. It is gone: the live dot, the
   progress bar and the stage log already carry that, and a clock counting up
   through a three-minute wait measures the wait rather than the work. */

function communitiesFrom(messages: string[]): string[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const hit = FOUND.exec(messages[index]);
    if (hit) {
      return hit[1]
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean);
    }
  }
  return [];
}

export function RunProgress({ messages }: { messages: string[] }) {
  const latest = messages[messages.length - 1] ?? "Starting…";
  const current = highestStage(messages);
  const communities = communitiesFrom(messages);
  const logRef = useRef<HTMLUListElement>(null);

  // The log is a tail, so it should behave like one: the newest line sits at the
  // bottom edge rather than out of sight below it.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages.length]);

  return (
    <section
      aria-label="Run progress"
      className="surface overflow-hidden"
      aria-busy="true"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-5 py-3.5 sm:px-6">
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="h-section flex items-center gap-2.5 text-foreground">
            <span aria-hidden="true" className="relative flex size-2.5">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--brand)] opacity-60" />
              <span className="relative inline-flex size-2.5 rounded-full bg-[var(--brand)]" />
            </span>
            Researching your topic
          </span>
          {/* The run now executes server-side and is polled from this page, so
              closing the tab no longer throws it away. It keeps going and shows
              up here again when you come back. */}
          <span className="text-xs leading-relaxed text-muted-foreground">
            You can close this tab. The run keeps going and will be here when you return.
          </span>
        </span>
        <span className="num text-xs text-muted-foreground">
          Step {current + 1} of {STAGES.length}
        </span>
      </div>

      {/* Determinate and honest: the bar tracks the stepper, which tracks the
          furthest stage the engine has actually announced. */}
      <div aria-hidden="true" className="h-0.5 w-full bg-[var(--viz-track)]">
        <div
          className="h-full bg-[var(--brand)] transition-[width] duration-500 ease-out"
          style={{ width: `${((current + 1) / STAGES.length) * 100}%` }}
        />
      </div>

      <div className="flex flex-col gap-5 px-5 py-5 sm:px-6">
        <ol className="flex list-none flex-col p-0">
          {STAGES.map((stage, index) => {
            const done = index < current;
            const active = index === current;
            const last = index === STAGES.length - 1;
            return (
              <li key={stage.id} className="flex items-start gap-3">
                {/* The rail: a dot and the segment of track below it, so the
                    five steps read as one journey rather than five bullets. */}
                <span
                  aria-hidden="true"
                  className="flex shrink-0 flex-col items-center self-stretch"
                >
                  <span
                    className={`flex size-4 items-center justify-center rounded-full border ${
                      done
                        ? "border-[var(--brand)] bg-[var(--brand)] text-white"
                        : active
                          ? "animate-pulse border-[var(--brand)] bg-[var(--brand-soft)]"
                          : "border-border bg-card"
                    }`}
                  >
                    {done ? <Check strokeWidth={2.25} className="size-2.5" /> : null}
                    {active ? (
                      <span className="size-1.5 rounded-full bg-[var(--brand)]" />
                    ) : null}
                  </span>
                  {last ? null : (
                    <span
                      className={`w-px flex-1 ${
                        done ? "bg-[var(--brand-line)]" : "bg-border"
                      }`}
                    />
                  )}
                </span>

                <div className={`flex min-w-0 flex-col ${last ? "" : "pb-4"}`}>
                  <span
                    className={`text-sm ${
                      active
                        ? "font-semibold text-foreground"
                        : done
                          ? "text-foreground-soft"
                          : "text-muted-foreground"
                    }`}
                  >
                    {stage.label}
                  </span>
                  {active ? (
                    <span
                      className="mt-1 text-xs leading-relaxed text-muted-foreground"
                      role="status"
                    >
                      {latest}
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>

        {communities.length > 0 ? (
          <div className="surface-brand flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
            <span className="h-quiet text-[var(--brand-ink)]">
              {communities.length === 1 ? "Community found" : "Communities found"}
            </span>
            <span className="flex flex-wrap items-center gap-1.5">
              {communities.map((name) => (
                <span key={name} className="chip">
                  {name}
                </span>
              ))}
            </span>
          </div>
        ) : null}

        {messages.length > 1 ? (
          <details className="border-t pt-4">
            <summary className="num cursor-pointer text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              Show all {messages.length} progress updates
            </summary>
            <ul
              ref={logRef}
              className="mt-3 flex max-h-48 list-none flex-col gap-1 overflow-y-auto rounded-lg bg-[var(--hush)] p-3 font-mono text-[0.6875rem] leading-relaxed text-muted-foreground"
            >
              {messages.map((message, index) => (
                <li key={`${index}-${message}`}>{message}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </section>
  );
}
