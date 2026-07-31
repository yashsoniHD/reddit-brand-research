"use client";

import { ArrowRight, Check } from "lucide-react";
import { useCallback, useState } from "react";

import { DraftDialog } from "@/components/report/draft-dialog";
import { EvidencePills } from "@/components/report/evidence-pills";
import {
  bundleChannels,
  channelsForFormat,
  type Bundle,
  type Channel,
  type ContentIdea,
} from "@/lib/types";

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
          {/* `h-quiet`, which is exactly what these classes were spelling out by
              hand. Its `uppercase` is inert on digits, and its letterspacing is
              the point of the label, so `num` comes off rather than fight it:
              the index is a fixed-width padded label, not a measured quantity. */}
          <span className="h-quiet text-[var(--brand-ink)]">
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
          {/* Set in the body face, like every other glyph in the product. This
              used to say `font-serif`, an undeclared face that fell back to
              Times, and it was the only non-Inter glyph in the app sitting on
              the hero element of the primary card. Inter's left double quote is
              a pair of upright wedges rather than Times' comma shapes, so it
              carries less optical weight and earns the size back (2.25rem), with
              the offsets cut for its squarer left sidebearing. */}
          <span
            aria-hidden="true"
            className="absolute -top-1.5 -left-1 text-4xl leading-none text-[var(--brand)]/25 select-none"
          >
            &ldquo;
          </span>
          <span className="h-zone block pl-5 text-balance text-foreground">{headline}</span>
        </span>

        {idea.hook ? (
          <span className="block text-xs leading-snug font-medium text-muted-foreground">
            {idea.title}
          </span>
        ) : null}

        {idea.angle ? (
          <span className="t-small block text-muted-foreground">{idea.angle}</span>
        ) : null}

        {/* Both icons at 2.25: they sit at the same size on the same line, and
            shipped at 2.5 and 2.25, which read as two different weights of the
            same idea. */}
        <span className="t-small mt-auto flex items-center gap-1.5 pt-3 font-semibold text-[var(--brand-ink)]">
          {drafted ? (
            <Check aria-hidden="true" strokeWidth={2.25} className="size-3.5" />
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
          citations are the card's proof, not a caption about it.

          Always rendered, even with nothing to show. Skipping it let a card
          without citations end flush while every neighbour ended with a 40px
          band, which ragged the grid edge at exactly the point where the
          product's core promise is missing. Saying so is the better answer. */}
      <span className="flex min-h-10 items-center gap-2 border-t bg-[var(--hush)] px-5 py-2.5">
        {citations.length > 0 ? (
          <>
            <span className="h-quiet">Sources</span>
            <EvidencePills citations={citations} label="Source" />
          </>
        ) : (
          <span className="h-quiet">No direct sources</span>
        )}
      </span>
    </article>
  );
}

/**
 * The in-page cache key for one draft.
 *
 * Keyed by channel as well as by idea, because the same idea written for
 * Instagram and for TikTok are two different posts and reopening either must not
 * show the other. The pipe cannot collide across indices: "1|" never prefixes
 * "10|".
 */
function draftKey(index: number, channel: Channel | undefined): string {
  return `${index}|${channel ?? ""}`;
}

/** Storage speaks JSON, where an object key is always a string. This turns
 *  {"0": "..."} into the cache the grid indexes with, dropping anything that is
 *  not a plain index.
 *
 *  Stored drafts are keyed by idea index alone (see the note on the database in
 *  the plan), so each one is seeded under `channel`, the channel the dialog opens
 *  on. A post written for a second channel is a fresh call, which is correct: no
 *  stored row can tell us it was ever written. */
function numericKeys(
  drafts: Record<string, string> | undefined,
  channel: Channel | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(drafts ?? {})) {
    const index = Number(key);
    if (Number.isInteger(index) && index >= 0 && typeof value === "string") {
      out[draftKey(index, channel)] = value;
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
  const channels = bundleChannels(bundle.inputs);
  const [open, setOpen] = useState<number | null>(null);
  // Which channel the dialog is drafting for. The run's first channel is the
  // default, and undefined means the run was channel-neutral.
  const [channel, setChannel] = useState<Channel | undefined>(channels[0]);

  // Only the channels that can ship THIS idea's format. A run for Instagram and
  // TikTok yields carousels, and a carousel cannot be posted to TikTok, so the
  // switcher must not offer it for that idea.
  const eligible = channelsForFormat(
    channels,
    open === null ? undefined : ideas[open]?.format,
  );
  // The selection is per-report but eligibility is per-idea, so a channel carried
  // over from a previous card may not apply to this one. Derived rather than
  // stored, which keeps the two from disagreeing.
  const active =
    channel && eligible.includes(channel) ? channel : eligible[0];
  // Cached per rendered report: reopening a card must not re-bill the model.
  // Seeded from storage, so a report opened from a link shows the posts that
  // were already written for it, marked as drafted, without a single call.
  const [drafts, setDrafts] = useState<Record<string, string>>(() =>
    numericKeys(initialDrafts, channels[0]),
  );

  const cache = useCallback(
    (post: string) => {
      setDrafts((current) =>
        open === null ? current : { ...current, [draftKey(open, active)]: post },
      );
    },
    [open, active],
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
            // Drafted in ANY channel counts: the card is telling the reader
            // there is already a post behind it, not which channel it is for.
            drafted={Object.keys(drafts).some((key) =>
              key.startsWith(`${index}|`),
            )}
            onOpen={() => setOpen(index)}
          />
        ))}
      </div>

      <DraftDialog
        idea={open === null ? null : ideas[open]}
        ideaIndex={open}
        // The channel is part of the key, so switching it remounts the body and
        // the mount effect fires the fresh request, exactly as reopening does.
        ideaKey={
          open === null ? null : `${bundle.generated_ts}-${open}-${active ?? ""}`
        }
        context={{
          query: bundle.inputs.query,
          profile: bundle.inputs.profile,
          reportId: bundle.report_id,
          channels: eligible,
        }}
        channel={active}
        onChannelChange={setChannel}
        cached={open === null ? undefined : drafts[draftKey(open, active)]}
        onCache={cache}
        onClose={() => setOpen(null)}
      />
    </>
  );
}
