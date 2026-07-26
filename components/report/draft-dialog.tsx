"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { draftPost } from "@/lib/api";
import { truncate } from "@/lib/format";
import { MAX_DRAFT_EVIDENCE, type ContentIdea } from "@/lib/types";

/**
 * Click an idea, get a post you can publish.
 *
 * Drafting starts the moment the dialog opens, so the common path is one click
 * and a wait rather than a click and then a second click. Results are cached by
 * the parent per rendered report, so reopening a card never bills a second call.
 */

export interface DraftContext {
  query: string;
  profile: string;
}

type State =
  | { phase: "loading" }
  | { phase: "ready"; post: string }
  | { phase: "error"; message: string };

function evidenceFor(idea: ContentIdea): string[] {
  return (idea.citations ?? [])
    .slice(0, MAX_DRAFT_EVIDENCE)
    .map((citation) => citation.text)
    .filter(Boolean);
}

/**
 * The body is keyed on the open idea, so each open is a fresh mount. That lets
 * a cached post be the initial state rather than something an effect has to
 * patch in after the first paint.
 */
function DraftBody({
  idea,
  context,
  cached,
  onCache,
}: {
  idea: ContentIdea;
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
      },
      abort.signal,
    );

    if (outcome.kind === "aborted") return;
    if (outcome.kind === "post") {
      setState({ phase: "ready", post: outcome.post });
      onCache(outcome.post);
      return;
    }
    setState({ phase: "error", message: outcome.message });
  }, [idea, context.query, context.profile, onCache]);

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
      <DialogHeader className="border-b px-6 py-5 text-left">
        <DialogTitle className="pr-8 text-base leading-snug font-semibold">
          {idea.title}
        </DialogTitle>
        <DialogDescription className="text-xs">
          Written from this idea and the posts behind it. Edit it to sound like you before
          publishing.
        </DialogDescription>
      </DialogHeader>

      <div className="max-h-[52svh] overflow-y-auto px-6 py-5">
        {state.phase === "loading" ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <p className="text-sm text-muted-foreground" role="status">
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
            className="rounded-xl border border-destructive/40 bg-destructive/8 px-4 py-3"
          >
            <p className="text-sm leading-relaxed text-foreground">{state.message}</p>
          </div>
        ) : (
          // A publish-look preview: the model's line breaks are the post's line
          // breaks, so they are preserved exactly rather than collapsed.
          <article className="surface-hush px-5 py-5">
            <p className="text-[0.9375rem] leading-relaxed whitespace-pre-wrap text-foreground">
              {state.post}
            </p>
          </article>
        )}

        {copyError ? (
          <p role="alert" className="mt-3 text-xs text-destructive">
            {copyError}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t px-6 py-4">
        <button
          type="button"
          onClick={() => void copy()}
          disabled={state.phase !== "ready"}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-45"
        >
          {copied ? "Copied" : "Copy post"}
        </button>
        <button
          type="button"
          onClick={regenerate}
          disabled={state.phase === "loading"}
          className="rounded-lg border px-4 py-2 text-sm font-medium hover:bg-secondary disabled:opacity-45"
        >
          {state.phase === "error" ? "Try again" : "Regenerate"}
        </button>
        <span aria-live="polite" className="ml-auto text-xs text-muted-foreground">
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
  ideaKey,
  context,
  cached,
  onCache,
  onClose,
}: {
  idea: ContentIdea | null;
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
        className="max-h-[88svh] gap-0 overflow-hidden p-0 sm:max-w-2xl"
      >
        {idea ? (
          <DraftBody
            key={ideaKey ?? idea.title}
            idea={idea}
            context={context}
            cached={cached}
            onCache={onCache}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
