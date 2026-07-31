"use client";

import { ChevronRight, Loader2, Search } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { joinQuoted, phraseDropReason } from "@/lib/format";
import { parsePhrases } from "@/lib/keywords";
import { checkCommunities } from "@/lib/validate";
import {
  CHANNEL_CHOICES,
  CHANNEL_LABELS,
  CONTENT_TYPE_CHOICES,
  DEFAULT_DAYS,
  formatsForChannels,
  MAX_PROFILE_CHARS,
  MAX_QUERY_CHARS,
  TIME_WINDOWS,
  type Channel,
  type ContentType,
} from "@/lib/types";

export interface FormValues {
  query: string;
  profile: string;
  /** Settings panel: comma-separated community names; empty means auto-discover. */
  subreddits: string;
  /** Settings panel: 7, 14 or 30. */
  days: number;
  /** Settings panel: whether the relevance triage runs. */
  relevance: boolean;
  /** Settings panel: the shapes the ideas may take. Empty sends nothing, meaning any. */
  contentTypes: ContentType[];
  /** Settings panel: where the user publishes. Empty means channel-neutral. */
  channels: Channel[];
}

function contentTypeLabel(value: ContentType): string {
  return CONTENT_TYPE_CHOICES.find((choice) => choice.value === value)?.label ?? value;
}

/** A short trace of the non-default settings, shown on the collapsed tab. */
function advancedSummary(values: FormValues): string {
  const parts: string[] = [];
  // The profile lives inside the panel now, so a filled one has to show on the
  // closed tab: a setting that changes the output must never hide silently.
  if (values.profile.trim()) parts.push("about you");
  // Named before the communities and the window: it is the setting that changes
  // the output most, so it is the one worth the characters.
  if (values.channels.length === 1) {
    parts.push(CHANNEL_LABELS[values.channels[0]]);
  } else if (values.channels.length > 1) {
    parts.push(`${values.channels.length} channels`);
  }
  const communities = values.subreddits
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  if (communities.length > 0) {
    parts.push(
      communities.length === 1
        ? "1 community"
        : `${communities.length} communities`,
    );
  }
  if (values.days !== DEFAULT_DAYS) parts.push(`last ${values.days} days`);
  if (!values.relevance) parts.push("relevance filter off");
  // One format is worth naming; several are not worth spelling out on a line
  // that also has to hold the communities and the time range.
  if (values.contentTypes.length === 1) {
    parts.push(contentTypeLabel(values.contentTypes[0]).toLowerCase());
  } else if (values.contentTypes.length > 1) {
    parts.push(`${values.contentTypes.length} content types`);
  }
  return parts.join(" · ");
}

/**
 * The one grouped-control look, shared by the channel checkboxes, the content
 * type checkboxes and the time range radios.
 *
 * Before this the panel had two idioms (raw native checkboxes and a segmented
 * control) for three controls that do the same kind of job, which read as two
 * unrelated features rather than one settings panel.
 */
// `.chip-control` owns the look now, including the 44px minimum target. These
// three rows are the densest interactive surface in the app and the one most
// often hit with a thumb, and at the ~32px this file used to draw they were
// under every touch-target floor going. The class also paints its own selected
// state off `data-selected`, so the local ON/OFF class lists are gone with it.
const CHIP = "chip-control";
// A chip cannot be re-ticked mid-run, and it has to look that way: a control
// that silently ignores a tap is worse than one that says it is unavailable.
const CHIP_DISABLED = "pointer-events-none opacity-45";

/**
 * A multi-select chip that is still a real checkbox.
 *
 * The input is `sr-only` rather than replaced, so the group keeps its native
 * keyboard behaviour and a screen reader still announces it as a checkbox. That
 * makes the focus ring on the label load-bearing: without it, a keyboard user
 * has nothing at all to tell them where they are.
 */
function ToggleChip({
  name,
  value,
  label,
  checked,
  disabled,
  onChange,
}: {
  name: string;
  value: string;
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      // `.chip-control` reads its selected state from here rather than from a
      // class this file picks, so every chip in the app tints the same way.
      data-selected={checked}
      className={cn(
        CHIP,
        disabled && CHIP_DISABLED,
        "has-focus-visible:ring-2 has-focus-visible:ring-[var(--ring)] has-focus-visible:ring-offset-1",
      )}
    >
      <input
        type="checkbox"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="sr-only"
      />
      {label}
    </label>
  );
}

/**
 * The muted explainer that sits under a field label.
 *
 * Capped in ch rather than left to the form's width: these hints run to two and
 * three sentences, and at 13px across the full 4xl column a line reaches roughly
 * 120 characters, which is twice the measure a reader can track back from.
 */
function FieldHint({ children }: { children: React.ReactNode }) {
  return (
    <p className="max-w-[68ch] text-[0.8125rem] leading-relaxed text-muted-foreground">
      {children}
    </p>
  );
}

/**
 * The query field's entire discoverability for keyword filtering: no new
 * field exists, so this single line is the only place that says quoting a
 * phrase changes what gets read.
 */
function phraseFeedback(query: string): string {
  const { phrases, dropped } = parsePhrases(query);

  if (dropped.length > 0) {
    const [first, ...rest] = dropped;
    const more = rest.length > 0 ? ` (${rest.length} more not used)` : "";
    const reason = `"${first}" was not used: ${phraseDropReason(first)}.${more}`;
    return phrases.length > 0
      ? `Reading only threads that mention ${joinQuoted(phrases)}. ${reason}`
      : reason;
  }

  if (phrases.length > 0) {
    return `Reading only threads that mention ${joinQuoted(phrases)}.`;
  }

  return `Tip: put "quotes" around a phrase to read only threads that mention it.`;
}

export function QueryForm({
  values,
  onChange,
  onSubmit,
  onCancel,
  running,
  advancedOpen,
  onAdvancedOpenChange,
  highlightCommunities = false,
  queryError,
}: {
  values: FormValues;
  onChange: (next: FormValues) => void;
  onSubmit: () => void;
  onCancel: () => void;
  running: boolean;
  /** Owned by the parent so a failed run can open the panel it complained about. */
  advancedOpen: boolean;
  onAdvancedOpenChange: (open: boolean) => void;
  /** Set after a submit that failed on the communities field. */
  highlightCommunities?: boolean;
  /**
   * The one validation message that belongs to the query field, from the
   * parent's `validate`. The banner still carries it; this puts it where the
   * mistake is, and gives the field something to point `aria-describedby` at.
   */
  queryError?: string;
}) {
  const [communitiesTouched, setCommunitiesTouched] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const communityErrorId = useId();
  const queryErrorId = useId();

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    onChange({ ...values, [key]: value });

  const summary = advancedSummary(values);
  const communities = checkCommunities(values.subreddits);
  const communityInvalid =
    communities.messages.length > 0 && (communitiesTouched || highlightCommunities);

  // The formats the ticked channels can actually ship. With no channels ticked
  // this is the whole menu, which is how the panel behaved before channels
  // existed.
  const allowed = new Set(formatsForChannels(values.channels));
  const shippable = CONTENT_TYPE_CHOICES.filter((choice) => allowed.has(choice.value));

  /**
   * Ticking a channel can invalidate a format already ticked, so both are
   * written in one update.
   *
   * A tick the run would not honour is worse than a tick that disappears: the
   * engine resolves a channel/format conflict in favour of the channel, so a
   * stranded "Poll" next to TikTok would be the form promising something the
   * report then quietly overrides.
   */
  const setChannel = (channel: Channel, checked: boolean) => {
    // Rebuilt from the menu rather than appended to, so the value always reads
    // in the order the chips are shown.
    const nextChannels = CHANNEL_CHOICES.filter((option) =>
      option.value === channel ? checked : values.channels.includes(option.value),
    ).map((option) => option.value);
    const stillShippable = new Set(formatsForChannels(nextChannels));
    onChange({
      ...values,
      channels: nextChannels,
      contentTypes: values.contentTypes.filter((type) => stillShippable.has(type)),
    });
  };

  const setContentType = (type: ContentType, checked: boolean) => {
    set(
      "contentTypes",
      CONTENT_TYPE_CHOICES.filter((option) =>
        option.value === type ? checked : values.contentTypes.includes(option.value),
      ).map((option) => option.value),
    );
  };

  /**
   * Collapsing the panel is also a way of leaving the field, and a mistake that
   * hides itself is worse than one that argues. The element is reopened
   * imperatively: React cannot re-drive `open` back to a value it already holds.
   */
  const onToggle = (open: boolean) => {
    if (!open && communities.messages.length > 0) {
      setCommunitiesTouched(true);
      if (detailsRef.current) detailsRef.current.open = true;
      onAdvancedOpenChange(true);
      return;
    }
    onAdvancedOpenChange(open);
  };

  return (
    <form
      className="surface-raised flex flex-col gap-6 p-5 sm:p-7"
      // The app writes its own validation messages, in its own words, in one
      // banner. The browser's native bubble would pre-empt them and say
      // something else, so it is switched off rather than argued with.
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      {/* The one field that matters, given the room to look like it. */}
      <div className="flex flex-col gap-2">
        <Label htmlFor="query" className="h-section text-foreground">
          What do you want content ideas about?
        </Label>
        <Textarea
          id="query"
          name="query"
          rows={3}
          maxLength={MAX_QUERY_CHARS}
          value={values.query}
          onChange={(event) => set("query", event.target.value)}
          // Editing the brief mid-run cannot reach the run: it is already in
          // flight with the values it was submitted with, and a field that
          // accepts a "fix" it will never apply is a lie about what happens next.
          disabled={running}
          aria-invalid={queryError ? true : undefined}
          aria-describedby={queryError ? queryErrorId : undefined}
          placeholder="e.g. Productivity struggles freelance designers have juggling multiple clients and deadlines"
          className="resize-y rounded-xl px-3.5 py-3 text-base leading-relaxed md:text-base"
        />
        {queryError ? (
          <p
            id={queryErrorId}
            className="text-[0.8125rem] leading-relaxed text-destructive"
          >
            {queryError}
          </p>
        ) : null}
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
            {phraseFeedback(values.query)}
          </p>
          <p className="num shrink-0 self-end text-xs text-muted-foreground">
            {values.query.length} of {MAX_QUERY_CHARS} characters
          </p>
        </div>
      </div>

      {/* Deliberately unfilled. This panel is nested inside a pure-white
          `surface-raised` card, where the page's three-plane stack has already
          bottomed out: the warm `--hush` plate read as a smudge on white, and a
          white-on-white plate had no containment at all. So it separates by rule
          and indentation instead, which is what globals.css says the elevation
          system is for. The submit row's own `border-t` closes the band, so there
          is no bottom rule here. */}
      <details
        ref={detailsRef}
        open={advancedOpen}
        onToggle={(event) => onToggle(event.currentTarget.open)}
        className="group border-t pt-4"
      >
        <summary className="flex cursor-pointer list-none items-center gap-2 marker:hidden">
          <ChevronRight
            aria-hidden="true"
            strokeWidth={2.25}
            className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-90"
          />
          <span className="h-section text-foreground">Settings</span>
          {summary ? (
            // `min-w-0` is what makes `truncate` work at all here. A flex item's
            // automatic minimum size is its max-content width, and `truncate`
            // sets white-space:nowrap, so without this the span refuses to
            // shrink below the full unwrapped summary (~630px of text in ~295px
            // of room at 375px) and pushes a horizontal scrollbar onto the page.
            <span className="ml-auto min-w-0 truncate text-xs text-[var(--brand-ink)]">
              {summary}
            </span>
          ) : (
            <span className="ml-auto hidden text-xs text-muted-foreground sm:inline">
              About you, channels, communities, time range
            </span>
          )}
        </summary>

        {/* The left rule is the only colour in the panel: it marks where the
            group starts and ends without filling anything. */}
        <div className="mt-4 flex flex-col gap-5 border-t border-l-2 border-l-[var(--brand-line)] pt-5 pl-4 sm:pl-5">
          {/* First in the panel because it is the one setting most runs want:
              the ideas are shaped for whoever is posting them. */}
          <div className="flex flex-col gap-2">
            <Label htmlFor="profile" className="h-section text-foreground">
              About you or your brand{" "}
              <span className="text-[0.8125rem] font-normal text-muted-foreground">
                optional
              </span>
            </Label>
            <FieldHint>
              Tailors the ideas to your voice and audience. Not sure? Paste your bio or
              homepage tagline.
            </FieldHint>
            <Textarea
              id="profile"
              name="profile"
              rows={2}
              maxLength={MAX_PROFILE_CHARS}
              value={values.profile}
              onChange={(event) => set("profile", event.target.value)}
              disabled={running}
              placeholder="I run a Notion template shop for freelance designers."
              className="resize-y rounded-xl px-3.5 py-2.5"
            />
          </div>

          {/* Channels sit directly above content types because one filters the
              other: ticking TikTok has to visibly shrink the format menu, and
              two settings that far apart would not read as connected. */}
          <fieldset className="flex flex-col gap-2 border-0 p-0">
            <legend className="h-section text-foreground">Channels</legend>
            <div className="flex flex-wrap gap-2 pt-0.5">
              {CHANNEL_CHOICES.map((choice) => (
                <ToggleChip
                  key={choice.value}
                  name="channels"
                  value={choice.value}
                  label={choice.label}
                  checked={values.channels.includes(choice.value)}
                  disabled={running}
                  onChange={(checked) => setChannel(choice.value, checked)}
                />
              ))}
            </div>
            <FieldHint>
              Writes the ideas and the drafts for where you actually post. Leave them
              all unticked for ideas that are not tied to one place.
            </FieldHint>
          </fieldset>

          <fieldset className="flex flex-col gap-2 border-0 p-0">
            <legend className="h-section text-foreground">Content types</legend>
            <div className="flex flex-wrap gap-2 pt-0.5">
              {shippable.map((choice) => (
                <ToggleChip
                  key={choice.value}
                  name="content-types"
                  value={choice.value}
                  label={choice.label}
                  checked={values.contentTypes.includes(choice.value)}
                  disabled={running}
                  onChange={(checked) => setContentType(choice.value, checked)}
                />
              ))}
            </div>
            <FieldHint>
              {values.channels.length > 0
                ? "Only the formats those channels can ship are offered. Keeps every idea inside the ones you tick, spread across them; leave them all unticked to let each idea pick its own."
                : "Keeps every idea inside the formats you tick, spread across them. Leave them all unticked to let each idea pick its own."}
            </FieldHint>
          </fieldset>

          <div className="flex flex-col gap-2">
            <Label htmlFor="subreddits" className="h-section text-foreground">
              Communities{" "}
              <span className="text-[0.8125rem] font-normal text-muted-foreground">
                optional
              </span>
            </Label>
            <FieldHint>
              Up to 8 subreddits, separated by commas. When set, these are used directly
              and automatic discovery is skipped.
            </FieldHint>
            <Input
              id="subreddits"
              name="subreddits"
              value={values.subreddits}
              onChange={(event) => set("subreddits", event.target.value)}
              onBlur={() => setCommunitiesTouched(true)}
              disabled={running}
              aria-invalid={communityInvalid || undefined}
              aria-describedby={communityInvalid ? communityErrorId : undefined}
              placeholder="e.g. freelance, web_design"
              autoComplete="off"
              spellCheck={false}
              className="h-10 rounded-lg bg-card px-3"
            />
            {communityInvalid ? (
              <div id={communityErrorId} className="flex flex-col gap-1">
                {communities.messages.map((message) => (
                  <p
                    key={message}
                    className="text-[0.8125rem] leading-relaxed text-destructive"
                  >
                    {message}
                  </p>
                ))}
              </div>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <Label className="h-section text-foreground">Time range</Label>
            {/* Single-select, so these stay radios rather than becoming
                ToggleChips. The look is shared; the semantics are not. */}
            <div
              role="radiogroup"
              aria-label="Time range"
              className="flex flex-wrap gap-2 pt-0.5"
            >
              {TIME_WINDOWS.map((days) => (
                <button
                  key={days}
                  type="button"
                  role="radio"
                  aria-checked={values.days === days}
                  // `.chip-control` tints from this, not from `aria-checked`:
                  // one attribute for the look across checkbox and radio chips.
                  data-selected={values.days === days}
                  disabled={running}
                  onClick={() => set("days", days)}
                  className={cn(
                    "num",
                    CHIP,
                    "disabled:pointer-events-none disabled:opacity-45",
                  )}
                >
                  {days} days
                </button>
              ))}
            </div>
            <FieldHint>
              {values.subreddits.trim()
                ? 'How far back to read the communities you named. 7 days favours what is being said right now. Putting "quotes" around a phrase searches them for it instead, which this range does not limit.'
                : "Only applies if we fall back to reading communities in full. Normally we search them for the threads that match your brief, however far back the matches reach."}
            </FieldHint>
          </div>

          <div className="flex flex-col gap-1.5">
            {/* Disabled with the rest of the form mid-run: it is the same
                promise the chips make, and a switch that flips without changing
                the run in flight is the worst of the lot, because it names a
                stage the log is about to walk past. */}
            <label
              className={cn(
                "flex cursor-pointer items-center gap-2.5 text-sm font-semibold text-foreground",
                running && CHIP_DISABLED,
              )}
            >
              <input
                type="checkbox"
                checked={values.relevance}
                disabled={running}
                onChange={(event) => set("relevance", event.target.checked)}
                className="size-4 accent-[var(--brand)]"
              />
              Relevance filter
            </label>
            <p className="pl-[1.625rem] text-[0.8125rem] leading-relaxed text-muted-foreground">
              Rates every candidate post and comment against your search before ranking,
              so off-topic viral threads do not crowd out on-topic quiet ones. Turning it
              off makes runs faster and selects by community engagement and recency
              alone.
            </p>
          </div>
        </div>
      </details>

      <div className="flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center">
        <Button
          type="submit"
          size="lg"
          disabled={running}
          // The trigger of a one-to-three-minute job has to carry the activity
          // itself: a disabled button with a blank icon slot reads as broken,
          // not as busy, to a sighted user and says nothing at all to a screen
          // reader.
          aria-busy={running}
          className="h-11 gap-2 rounded-xl px-5 text-[0.9375rem] sm:w-auto"
        >
          {running ? (
            <Loader2
              aria-hidden="true"
              strokeWidth={2}
              className="size-4 animate-spin"
            />
          ) : (
            <Search aria-hidden="true" strokeWidth={2} className="size-4" />
          )}
          {running ? "Working…" : "Find content ideas"}
        </Button>
        {running ? (
          <Button
            type="button"
            variant="ghost"
            size="lg"
            onClick={onCancel}
            className="h-11 rounded-xl px-4"
          >
            Cancel
          </Button>
        ) : null}
        {/* Stays put while the run goes. The estimate used to be swapped out for
            the Cancel button at exactly the moment it became useful: before the
            run it is a promise, during it it is the only thing telling the
            reader that two minutes of nothing is normal. */}
        <p className="text-xs leading-relaxed text-muted-foreground">
          Usually takes 1 to 3 minutes.
        </p>
      </div>
    </form>
  );
}
