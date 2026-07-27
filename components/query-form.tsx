"use client";

import { ChevronRight, Search } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { checkCommunities } from "@/lib/validate";
import {
  CONTENT_TYPE_CHOICES,
  DEFAULT_DAYS,
  MAX_PROFILE_CHARS,
  MAX_QUERY_CHARS,
  TIME_WINDOWS,
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

/** The muted explainer that sits under a field label. */
function FieldHint({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">{children}</p>
  );
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
}) {
  const [communitiesTouched, setCommunitiesTouched] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const communityErrorId = useId();

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    onChange({ ...values, [key]: value });

  const summary = advancedSummary(values);
  const communities = checkCommunities(values.subreddits);
  const communityInvalid =
    communities.messages.length > 0 && (communitiesTouched || highlightCommunities);

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
          What do you want LinkedIn ideas about?
        </Label>
        <Textarea
          id="query"
          name="query"
          rows={3}
          maxLength={MAX_QUERY_CHARS}
          value={values.query}
          onChange={(event) => set("query", event.target.value)}
          placeholder="e.g. Productivity struggles freelance designers have juggling multiple clients and deadlines"
          className="resize-y rounded-xl px-3.5 py-3 text-base leading-relaxed md:text-base"
        />
        <p className="num self-end text-xs text-muted-foreground">
          {values.query.length} of {MAX_QUERY_CHARS} characters
        </p>
      </div>

      <details
        ref={detailsRef}
        open={advancedOpen}
        onToggle={(event) => onToggle(event.currentTarget.open)}
        className="group surface-hush px-4 py-3 sm:px-5"
      >
        <summary className="flex cursor-pointer list-none items-center gap-2 marker:hidden">
          <ChevronRight
            aria-hidden="true"
            strokeWidth={2.25}
            className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-90"
          />
          <span className="text-sm font-medium text-foreground">Settings</span>
          {summary ? (
            <span className="ml-auto truncate text-xs text-[var(--brand-ink)]">
              {summary}
            </span>
          ) : (
            <span className="ml-auto hidden text-xs text-muted-foreground sm:inline">
              About you, communities, format, time range
            </span>
          )}
        </summary>

        <div className="mt-5 flex flex-col gap-6 border-t pt-5">
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
              placeholder="I run a Notion template shop for freelance designers."
              className="resize-y rounded-xl px-3.5 py-2.5"
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="subreddits" className="h-section text-foreground">
              Communities{" "}
              <span className="text-[0.8125rem] font-normal text-muted-foreground">
                optional
              </span>
            </Label>
            <FieldHint>
              Up to 4 subreddits, separated by commas. When set, these are read directly
              and automatic discovery is skipped.
            </FieldHint>
            <Input
              id="subreddits"
              name="subreddits"
              value={values.subreddits}
              onChange={(event) => set("subreddits", event.target.value)}
              onBlur={() => setCommunitiesTouched(true)}
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

          <fieldset className="flex flex-col gap-2 border-0 p-0">
            <legend className="h-section text-foreground">Content types</legend>
            {/* Checkboxes rather than a menu: picking two formats is a normal
                thing to want, and the group has to say so at a glance. */}
            <div className="flex flex-wrap gap-x-5 gap-y-2.5 pt-0.5">
              {CONTENT_TYPE_CHOICES.map((choice) => (
                <label
                  key={choice.value}
                  className="flex cursor-pointer items-center gap-2 text-sm text-foreground"
                >
                  <input
                    type="checkbox"
                    name="content-types"
                    value={choice.value}
                    checked={values.contentTypes.includes(choice.value)}
                    onChange={(event) =>
                      set(
                        "contentTypes",
                        // Rebuilt from the menu rather than appended to, so the
                        // value always reads in the order the boxes are shown.
                        CONTENT_TYPE_CHOICES.filter((option) =>
                          option.value === choice.value
                            ? event.target.checked
                            : values.contentTypes.includes(option.value),
                        ).map((option) => option.value),
                      )
                    }
                    className="size-4 accent-[var(--brand)]"
                  />
                  {choice.label}
                </label>
              ))}
            </div>
            <FieldHint>
              Keeps every idea inside the formats you tick, spread across them.
              Leave them all unticked to let each idea pick its own.
            </FieldHint>
          </fieldset>

          <div className="flex flex-col gap-2">
            <Label className="h-section text-foreground">Time range</Label>
            {/* A segmented control: one track, one filled cell, no gaps to
                misread as separate buttons. */}
            <div
              role="radiogroup"
              aria-label="Time range"
              className="flex w-fit gap-1 rounded-xl border bg-card p-1"
            >
              {TIME_WINDOWS.map((days) => (
                <button
                  key={days}
                  type="button"
                  role="radio"
                  aria-checked={values.days === days}
                  onClick={() => set("days", days)}
                  className={cn(
                    "num rounded-lg px-3.5 py-1.5 text-sm transition-colors",
                    values.days === days
                      ? "bg-[var(--brand-soft)] font-semibold text-[var(--brand-ink)] shadow-[inset_0_0_0_1px_var(--brand-line)]"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                  )}
                >
                  {days} days
                </button>
              ))}
            </div>
            <FieldHint>
              How far back to read. 30 days is the default and usually the best read;
              shorter ranges favour what is being said right now.
            </FieldHint>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="flex cursor-pointer items-center gap-2.5 text-sm font-semibold text-foreground">
              <input
                type="checkbox"
                checked={values.relevance}
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
          className="h-11 gap-2 rounded-xl px-5 text-[0.9375rem] sm:w-auto"
        >
          {running ? null : (
            <Search aria-hidden="true" strokeWidth={2.25} className="size-4" />
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
        ) : (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Usually takes 1 to 3 minutes.
          </p>
        )}
      </div>
    </form>
  );
}
