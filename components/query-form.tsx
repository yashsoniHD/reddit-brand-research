"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  DEFAULT_DAYS,
  MAX_PROFILE_CHARS,
  MAX_QUERY_CHARS,
  TIME_WINDOWS,
} from "@/lib/types";

export interface FormValues {
  query: string;
  profile: string;
  /** Advanced: comma-separated community names; empty means auto-discover. */
  subreddits: string;
  /** Advanced: 7, 14 or 30. */
  days: number;
  /** Advanced: whether the relevance triage runs. */
  relevance: boolean;
}

/** A short trace of non-default advanced settings, shown on the collapsed tab. */
function advancedSummary(values: FormValues): string {
  const parts: string[] = [];
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
  return parts.join(" · ");
}

export function QueryForm({
  values,
  onChange,
  onSubmit,
  onCancel,
  running,
}: {
  values: FormValues;
  onChange: (next: FormValues) => void;
  onSubmit: () => void;
  onCancel: () => void;
  running: boolean;
}) {
  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) =>
    onChange({ ...values, [key]: value });

  const summary = advancedSummary(values);

  return (
    <form
      className="surface flex flex-col gap-5 p-6 sm:p-7"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="query" className="text-sm font-medium">
          What do you want content ideas about?
        </Label>
        <Textarea
          id="query"
          name="query"
          rows={3}
          maxLength={MAX_QUERY_CHARS}
          required
          value={values.query}
          onChange={(event) => set("query", event.target.value)}
          placeholder="e.g. Productivity struggles freelance designers have juggling multiple clients and deadlines"
          className="resize-y text-base"
        />
        <p className="text-xs tabular-nums text-muted-foreground">
          {values.query.length} of {MAX_QUERY_CHARS} characters
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="profile" className="text-sm font-medium">
          About you or your brand{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Tailors the ideas to your voice and audience. Not sure? Paste your bio or
          homepage tagline.
        </p>
        <Textarea
          id="profile"
          name="profile"
          rows={2}
          maxLength={MAX_PROFILE_CHARS}
          value={values.profile}
          onChange={(event) => set("profile", event.target.value)}
          placeholder="I run a Notion template shop for freelance designers."
          className="resize-y"
        />
      </div>

      <details className="group rounded-lg border border-dashed px-4 py-3">
        <summary className="flex cursor-pointer list-none items-center gap-2 marker:hidden">
          <span
            aria-hidden="true"
            className="text-xs text-muted-foreground transition-transform duration-200 group-open:rotate-90"
          >
            &rsaquo;
          </span>
          <span className="text-sm font-medium text-foreground">
            Advanced settings
          </span>
          {summary ? (
            <span className="ml-auto text-xs text-muted-foreground">{summary}</span>
          ) : null}
        </summary>

        <div className="mt-4 flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor="subreddits" className="text-sm font-medium">
              Communities{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <p className="text-xs leading-relaxed text-muted-foreground">
              Up to 4 subreddits, separated by commas. When set, these are read
              directly and automatic discovery is skipped.
            </p>
            <Input
              id="subreddits"
              name="subreddits"
              value={values.subreddits}
              onChange={(event) => set("subreddits", event.target.value)}
              placeholder="e.g. freelance, web_design"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label className="text-sm font-medium">Time range</Label>
            <div
              role="radiogroup"
              aria-label="Time range"
              className="flex w-fit overflow-hidden rounded-lg border"
            >
              {TIME_WINDOWS.map((days) => (
                <button
                  key={days}
                  type="button"
                  role="radio"
                  aria-checked={values.days === days}
                  onClick={() => set("days", days)}
                  className={cn(
                    "px-3.5 py-1.5 text-sm transition-colors not-last:border-r",
                    values.days === days
                      ? "bg-secondary font-medium text-foreground"
                      : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
                  )}
                >
                  {days} days
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              How far back to read. 30 days is the default and usually the best
              read; shorter ranges favour what is being said right now.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-foreground">
              <input
                type="checkbox"
                checked={values.relevance}
                onChange={(event) => set("relevance", event.target.checked)}
                className="size-4 accent-primary"
              />
              Relevance filter
            </label>
            <p className="pl-6 text-xs leading-relaxed text-muted-foreground">
              Rates every candidate post and comment against your search before
              ranking, so off-topic viral threads do not crowd out on-topic quiet
              ones. Turning it off makes runs faster and selects by community
              engagement and recency alone.
            </p>
          </div>
        </div>
      </details>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Button type="submit" size="lg" disabled={running} className="sm:w-auto">
          {running ? "Working…" : "Find content ideas"}
        </Button>
        {running ? (
          <Button type="button" variant="ghost" size="lg" onClick={onCancel}>
            Cancel
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">Usually takes 30 to 60 seconds.</p>
        )}
      </div>
    </form>
  );
}
