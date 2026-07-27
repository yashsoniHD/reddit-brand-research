"use client";

import { ArrowRight, Check } from "lucide-react";
import { useCallback, useState } from "react";

import { DraftDialog } from "@/components/report/draft-dialog";
import { EvidencePills } from "@/components/report/evidence-pills";
import type { Bundle, ContentIdea } from "@/lib/types";

/**
 * The product, and therefore the top of the report.
 *
 * The hook is the headline because the hook is the thing the user will actually
 * publish; the title is a filing label and rides underneath it. The button
 * covers everything above the source rail: clicking the card drafts the post,
 * and the citations sit in a footer of their own because a link nested inside a
 * button is not a valid target.
 */

function IdeaCard({
  idea,
  index,
  drafted,
  onOpen,
}: {
  idea: ContentIdea;
  index: number;
  drafted: boolean;
  onOpen: () => void;
}) {
  const headline = idea.hook || idea.title;
  const citations = idea.citations ?? [];

  return (
    <article className="surface group relative flex h-full flex-col overflow-hidden transition-[box-shadow,transform,border-color] duration-200 hover:-translate-y-0.5 hover:border-[var(--brand-line)] hover:shadow-[var(--lift-2)] focus-within:border-[var(--brand-line)]">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Draft a post from: ${headline}`}
        className="flex flex-1 cursor-pointer flex-col gap-3 p-5 text-left"
      >
        <span className="flex items-center justify-between gap-3">
          <span className="num text-[0.6875rem] font-semibold tracking-[0.075em] text-[var(--brand-ink)]">
            {String(index + 1).padStart(2, "0")}
          </span>
          {idea.format ? (
            <span className="chip border-transparent bg-secondary text-[0.6875rem] text-secondary-foreground">
              {idea.format}
            </span>
          ) : null}
        </span>

        {/* The hook, quoted, in the largest type on the card. */}
        <span className="relative block pt-1">
          <span
            aria-hidden="true"
            className="absolute -top-2 -left-1 font-serif text-4xl leading-none text-[var(--brand)]/25 select-none"
          >
            &ldquo;
          </span>
          <span className="block pl-5 text-[1.0625rem] leading-[1.35] font-semibold tracking-[-0.014em] text-balance text-foreground">
            {headline}
          </span>
        </span>

        {idea.hook ? (
          <span className="block text-xs leading-snug font-medium text-muted-foreground">
            {idea.title}
          </span>
        ) : null}

        {idea.angle ? (
          <span className="block text-[0.8125rem] leading-relaxed text-muted-foreground">
            {idea.angle}
          </span>
        ) : null}

        <span className="mt-auto flex items-center gap-1.5 pt-3 text-[0.8125rem] font-semibold text-[var(--brand-ink)]">
          {drafted ? (
            <Check aria-hidden="true" strokeWidth={2.5} className="size-3.5" />
          ) : null}
          {drafted ? "View draft" : "Draft this post"}
          <ArrowRight
            aria-hidden="true"
            strokeWidth={2.25}
            className="size-3.5 transition-transform duration-200 group-hover:translate-x-0.5"
          />
        </span>
      </button>

      {/* Outside the button: a link inside a button is not a valid target. The
          rail is part of the card rather than floating below it, because the
          citations are the card's proof, not a caption about it. */}
      {citations.length > 0 ? (
        <span className="flex items-center gap-2 border-t bg-[var(--hush)] px-5 py-2.5">
          <span className="h-quiet">Sources</span>
          <EvidencePills citations={citations} label="Source" />
        </span>
      ) : null}
    </article>
  );
}

/** Storage speaks JSON, where an object key is always a string. This turns
 *  {"0": "..."} back into the number-keyed map the grid indexes with, dropping
 *  anything that is not a plain index. */
function numericKeys(drafts?: Record<string, string>): Record<number, string> {
  const out: Record<number, string> = {};
  for (const [key, value] of Object.entries(drafts ?? {})) {
    const index = Number(key);
    if (Number.isInteger(index) && index >= 0 && typeof value === "string") {
      out[index] = value;
    }
  }
  return out;
}

export function ContentIdeas({
  bundle,
  initialDrafts,
}: {
  bundle: Bundle;
  /** Posts already written for this report, keyed by idea index as a string.
   *  Only a saved report opened from its link has any. */
  initialDrafts?: Record<string, string>;
}) {
  const ideas = bundle.analysis?.content_ideas ?? [];
  const [open, setOpen] = useState<number | null>(null);
  // Cached per rendered report: reopening a card must not re-bill the model.
  // Seeded from storage, so a report opened from a link shows the posts that
  // were already written for it, marked as drafted, without a single call.
  const [drafts, setDrafts] = useState<Record<number, string>>(() =>
    numericKeys(initialDrafts),
  );

  const cache = useCallback(
    (post: string) => {
      setDrafts((current) => (open === null ? current : { ...current, [open]: post }));
    },
    [open],
  );

  if (ideas.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No content ideas were produced for this run.
      </p>
    );
  }

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {ideas.map((idea, index) => (
          <IdeaCard
            key={`${index}-${idea.title}`}
            idea={idea}
            index={index}
            drafted={drafts[index] !== undefined}
            onOpen={() => setOpen(index)}
          />
        ))}
      </div>

      <DraftDialog
        idea={open === null ? null : ideas[open]}
        ideaIndex={open}
        ideaKey={open === null ? null : `${bundle.generated_ts}-${open}`}
        context={{
          query: bundle.inputs.query,
          profile: bundle.inputs.profile,
          reportId: bundle.report_id,
        }}
        cached={open === null ? undefined : drafts[open]}
        onCache={cache}
        onClose={() => setOpen(null)}
      />
    </>
  );
}
