"use client";

import { useCallback, useState } from "react";

import { DraftDialog } from "@/components/report/draft-dialog";
import { EvidencePills } from "@/components/report/evidence-pills";
import type { Bundle, ContentIdea } from "@/lib/types";

/**
 * The product, and therefore the top of the report.
 *
 * The hook is the headline because the hook is the thing the user will actually
 * publish; the title is a filing label and rides underneath it. The whole card
 * is the button: clicking anywhere drafts the post.
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

  return (
    <article className="group relative flex flex-col">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Draft a post from: ${headline}`}
        className="surface flex h-full cursor-pointer flex-col gap-3 p-5 text-left transition-[box-shadow,transform,border-color] duration-200 hover:-translate-y-0.5 hover:border-[var(--brand)]/40 hover:shadow-[var(--lift-2)] focus-visible:border-[var(--brand)]/40"
      >
        <span className="flex items-center justify-between gap-3">
          <span className="h-quiet tabular-nums">
            {String(index + 1).padStart(2, "0")}
          </span>
          {idea.format ? (
            <span className="rounded-full bg-secondary px-2.5 py-0.5 text-[0.6875rem] font-medium text-secondary-foreground">
              {idea.format}
            </span>
          ) : null}
        </span>

        {/* The hook, quoted, in the largest type on the card. */}
        <span className="relative block pt-1">
          <span
            aria-hidden="true"
            className="absolute -top-1 -left-0.5 font-serif text-3xl leading-none text-[var(--brand)]/35 select-none"
          >
            &ldquo;
          </span>
          <span className="block pl-5 text-[1.0625rem] leading-snug font-semibold text-balance text-foreground">
            {headline}
          </span>
        </span>

        {idea.hook ? (
          <span className="block text-xs leading-snug font-medium text-muted-foreground">
            {idea.title}
          </span>
        ) : null}

        {idea.angle ? (
          <span className="block text-sm leading-relaxed text-muted-foreground">
            {idea.angle}
          </span>
        ) : null}

        <span className="mt-auto flex items-center justify-between gap-3 pt-2">
          <span className="text-[0.8125rem] font-medium text-[var(--brand-ink)]">
            {drafted ? "View draft" : "Draft this post"}
            <span
              aria-hidden="true"
              className="ml-1 inline-block transition-transform duration-200 group-hover:translate-x-0.5"
            >
              &rarr;
            </span>
          </span>
        </span>
      </button>

      {/* Outside the button: a link inside a button is not a valid target. */}
      {idea.citations.length > 0 ? (
        <span className="mt-2 flex items-center gap-1.5 pl-1">
          <span className="text-[0.6875rem] text-muted-foreground">From</span>
          <EvidencePills citations={idea.citations} label="Source" />
        </span>
      ) : null}
    </article>
  );
}

export function ContentIdeas({ bundle }: { bundle: Bundle }) {
  const ideas = bundle.analysis.content_ideas ?? [];
  const [open, setOpen] = useState<number | null>(null);
  // Cached per rendered report: reopening a card must not re-bill the model.
  const [drafts, setDrafts] = useState<Record<number, string>>({});

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
        ideaKey={open === null ? null : `${bundle.generated_ts}-${open}`}
        context={{ query: bundle.inputs.query, profile: bundle.inputs.profile }}
        cached={open === null ? undefined : drafts[open]}
        onCache={cache}
        onClose={() => setOpen(null)}
      />
    </>
  );
}
