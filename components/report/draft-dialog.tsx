"use client";

import { Check, Copy, RotateCw } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { EvidencePills } from "@/components/report/evidence-pills";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { draftPost, saveDraft } from "@/lib/api";
import { truncate } from "@/lib/format";
import { MAX_DRAFT_EVIDENCE, type ContentIdea } from "@/lib/types";

/**
 * Click an idea, get a post you can publish.
 *
 * Drafting starts the moment the dialog opens, so the common path is one click
 * and a wait rather than a click and then a second click. Results are cached by
 * the parent per rendered report, so reopening a card never bills a second call.
 *
 * When the report was saved, the cache is also a database row: generation is
 * told where to file the result, and an edit is written back, so the draft
 * survives a refresh and comes back with the report from its link.
 */

export interface DraftContext {
  query: string;
  profile: string;
  /** Set only when this report is stored. Absent means nothing persists. */
  reportId?: string;
}

type State =
  | { phase: "loading" }
  | { phase: "ready"; post: string }
  | { phase: "error"; message: string };

/**
 * What the writing call gets to work from.
 *
 * The excerpt is a real passage from the source item and the title is a label
 * for it, so the excerpt is always the better prompt material. Older bundles
 * carry only the title, which is why the fallback exists.
 */
function evidenceFor(idea: ContentIdea): string[] {
  return (idea.citations ?? [])
    .slice(0, MAX_DRAFT_EVIDENCE)
    .map((citation) => (citation.excerpt?.trim() ? citation.excerpt : citation.text))
    .filter(Boolean);
}

/**
 * The body is keyed on the open idea, so each open is a fresh mount. That lets
 * a cached post be the initial state rather than something an effect has to
 * patch in after the first paint.
 */
function DraftBody({
  idea,
  ideaIndex,
  context,
  cached,
  onCache,
}: {
  idea: ContentIdea;
  /** Which idea this is in the report, and therefore which row it saves to. */
  ideaIndex: number;
  context: DraftContext;
  cached: string | undefined;
  onCache: (post: string) => void;
}) {
  const [state, setState] = useState<State>(() =>
    cached ? { phase: "ready", post: cached } : { phase: "loading" },
  );
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const copyTimer = useRef<number | null>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  // What the database is known to hold, so a blur that changed nothing (the
  // common case: open, read, close) costs no request at all. Seeded with the
  // cached post, which either came back from the server or was written by it.
  const savedPost = useRef<string | undefined>(cached);
  const citations = (idea.citations ?? []).slice(0, MAX_DRAFT_EVIDENCE);

  /**
   * Persist an edit, best effort and silent.
   *
   * Blur is the debounce: it fires once per visit to the editor rather than
   * once per keystroke, which is exactly the granularity a save wants, and a
   * timer-based debounce would have to be flushed on unmount anyway, since
   * closing the dialog blurs the textarea and unmounts this body in the same
   * tick. Failure is deliberately invisible: the post is still in the page's
   * cache and still on screen, so an alarming banner would be describing a
   * problem the reader cannot act on and has not yet suffered.
   */
  const persist = useCallback(
    (post: string) => {
      const reportId = context.reportId;
      if (!reportId || !post.trim() || post === savedPost.current) return;
      savedPost.current = post;
      void saveDraft(reportId, ideaIndex, post);
    },
    [context.reportId, ideaIndex],
  );

  /**
   * The editor grows with the post instead of scrolling inside itself: the
   * dialog body already scrolls, and two nested scrollbars around one piece of
   * text is a worse reading experience than a long box.
   */
  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  }, [state]);

  /** Fires the request and records the outcome. Never sets the loading state:
   *  callers own that, which keeps the mount path free of a synchronous
   *  setState inside an effect. */
  const request = useCallback(async () => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;

    const outcome = await draftPost(
      {
        idea: {
          title: idea.title,
          hook: idea.hook,
          format: idea.format,
          angle: idea.angle,
          addresses: idea.addresses,
        },
        evidence: evidenceFor(idea),
        query: context.query,
        profile: context.profile,
        // Sent together or not at all: with them the server files the post it
        // just wrote, so nothing has to be saved from here afterwards.
        ...(context.reportId
          ? { report_id: context.reportId, idea_index: ideaIndex }
          : {}),
      },
      abort.signal,
    );

    if (outcome.kind === "aborted") return;
    if (outcome.kind === "post") {
      setState({ phase: "ready", post: outcome.post });
      savedPost.current = context.reportId ? outcome.post : undefined;
      onCache(outcome.post);
      return;
    }
    setState({ phase: "error", message: outcome.message });
  }, [idea, ideaIndex, context.query, context.profile, context.reportId, onCache]);

  // Opening is the trigger. A cached post is already the initial state, so this
  // only ever fires for an idea that has not been drafted yet.
  //
  // The lint rules below are both deliberate. `exhaustive-deps`: this is
  // mount-only by design, because reopening remounts (the body is keyed) and
  // Regenerate is an explicit user action. `set-state-in-effect`: the only
  // setState inside `request` happens after the fetch resolves, which is the
  // subscribe-to-an-external-system case the rule exists to allow, but the rule
  // cannot see past the function boundary to tell.
  /* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
  useEffect(() => {
    if (cached) return;
    void request();
  }, []);
  /* eslint-enable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */

  useEffect(
    () => () => {
      controller.current?.abort();
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
    },
    [],
  );

  const regenerate = () => {
    setState({ phase: "loading" });
    setCopied(false);
    setCopyError(null);
    void request();
  };

  const copy = async () => {
    if (state.phase !== "ready") return;
    try {
      await navigator.clipboard.writeText(state.post);
      setCopyError(null);
      setCopied(true);
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 2200);
    } catch {
      // Clipboard permission can be refused outright, and a silent no-op would
      // look like the button is broken. The draft itself is untouched.
      setCopied(false);
      setCopyError("Your browser blocked the clipboard. Select the post above and copy it.");
    }
  };

  const words =
    state.phase === "ready" ? state.post.trim().split(/\s+/).filter(Boolean).length : 0;

  return (
    <>
      <DialogHeader className="gap-1.5 border-b px-6 py-5 text-left">
        <span className="h-quiet">Your LinkedIn draft</span>
        <DialogTitle className="pr-8 text-[1.0625rem] leading-snug font-semibold tracking-[-0.014em]">
          {idea.title}
        </DialogTitle>
        <DialogDescription className="text-xs leading-relaxed">
          Written from this idea and the posts behind it. Edit it to sound like you before
          publishing.
        </DialogDescription>
      </DialogHeader>

      <div className="max-h-[52svh] overflow-y-auto px-6 py-5">
        {state.phase === "loading" ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <p
              className="flex items-center gap-2 text-sm text-muted-foreground"
              role="status"
            >
              <span aria-hidden="true" className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--brand)] opacity-60" />
                <span className="relative inline-flex size-2 rounded-full bg-[var(--brand)]" />
              </span>
              Writing your post…
            </p>
            {[92, 78, 96, 64, 88, 71].map((width, index) => (
              <span
                key={index}
                className="block h-3 animate-pulse rounded bg-muted"
                style={{ width: `${width}%` }}
              />
            ))}
          </div>
        ) : state.phase === "error" ? (
          <div
            role="alert"
            className="rounded-xl border border-destructive/35 bg-destructive/8 px-4 py-3"
          >
            <p className="text-sm leading-relaxed text-foreground">{state.message}</p>
          </div>
        ) : (
          // Editable in place, styled as the post rather than as a form field:
          // nobody publishes a first draft untouched, and making them copy it
          // out to change one line is a step this dialog can simply absorb.
          // Copy takes whatever is in here, edits included.
          <div className="surface-hush px-5 py-5 transition-colors focus-within:border-[var(--brand-line)]">
            <label htmlFor="draft-editor" className="sr-only">
              Your draft post, editable
            </label>
            <textarea
              id="draft-editor"
              ref={editorRef}
              value={state.post}
              onChange={(event) => {
                setState({ phase: "ready", post: event.target.value });
                setCopied(false);
              }}
              // Cached on blur rather than on every keystroke: reopening the
              // card should return the edited post, without re-rendering the
              // whole grid behind the dialog once per character. The same
              // moment is when the edit goes to the database, for a report
              // that has one.
              onBlur={(event) => {
                onCache(event.target.value);
                persist(event.target.value);
              }}
              spellCheck
              className="block w-full resize-none border-0 bg-transparent p-0 text-[0.9375rem] leading-[1.65] whitespace-pre-wrap text-foreground outline-none focus-visible:outline-none"
            />
          </div>
        )}

        {copyError ? (
          <p role="alert" className="mt-3 text-xs text-destructive">
            {copyError}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t bg-[var(--hush)] px-6 py-4">
        <button
          type="button"
          onClick={() => void copy()}
          disabled={state.phase !== "ready"}
          className="flex h-9 items-center gap-1.5 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85 disabled:opacity-45 disabled:hover:bg-primary"
        >
          {copied ? (
            <Check aria-hidden="true" strokeWidth={2.5} className="size-3.5" />
          ) : (
            <Copy aria-hidden="true" strokeWidth={2} className="size-3.5" />
          )}
          {copied ? "Copied" : "Copy post"}
        </button>
        <button
          type="button"
          onClick={regenerate}
          disabled={state.phase === "loading"}
          className="flex h-9 items-center gap-1.5 rounded-lg border bg-card px-3.5 text-sm font-medium transition-colors hover:bg-secondary disabled:opacity-45"
        >
          <RotateCw aria-hidden="true" strokeWidth={2} className="size-3.5" />
          {state.phase === "error" ? "Try again" : "Regenerate"}
        </button>

        {/* The report promises the sources behind the idea, so the dialog has
            to carry them: the card that opened it is now behind an overlay. */}
        {citations.length > 0 ? (
          <span className="flex items-center gap-1.5">
            <span className="h-quiet">Sources</span>
            <EvidencePills citations={citations} label="Source" />
          </span>
        ) : null}

        <span aria-live="polite" className="num ml-auto text-xs text-muted-foreground">
          {copied
            ? "Copied to your clipboard"
            : state.phase === "ready"
              ? `${words} words`
              : idea.format
                ? truncate(idea.format, 40)
                : ""}
        </span>
      </div>
    </>
  );
}

export function DraftDialog({
  idea,
  ideaIndex,
  ideaKey,
  context,
  cached,
  onCache,
  onClose,
}: {
  idea: ContentIdea | null;
  /** Position of the open idea in the report. Null when nothing is open. */
  ideaIndex: number | null;
  /** Identity of the open idea, so each open is its own mount. */
  ideaKey: string | null;
  context: DraftContext;
  cached: string | undefined;
  onCache: (post: string) => void;
  onClose: () => void;
}) {
  return (
    <Dialog open={idea !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton
        className="max-h-[88svh] gap-0 overflow-hidden rounded-2xl p-0 shadow-[var(--lift-3)] sm:max-w-2xl"
      >
        {idea ? (
          <DraftBody
            key={ideaKey ?? idea.title}
            idea={idea}
            ideaIndex={ideaIndex ?? 0}
            context={context}
            cached={cached}
            onCache={onCache}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
