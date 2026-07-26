"use client";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  MAX_PROFILE_CHARS,
  MAX_QUERY_CHARS,
  PLATFORM_LABELS,
  TIME_WINDOWS,
  type PlatformKey,
} from "@/lib/types";

export interface FormValues {
  query: string;
  profile: string;
  platform: PlatformKey;
  days: number;
}

const PLATFORM_OPTIONS: PlatformKey[] = ["linkedin", "instagram", "both"];

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

  return (
    <form
      className="flex flex-col gap-5 rounded-2xl border bg-card p-6 shadow-[0_1px_2px_rgba(11,11,11,0.04)] sm:p-7"
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

      <div className="grid gap-5 sm:grid-cols-2">
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-2 text-sm font-medium text-foreground">Platform</legend>
          <div
            className="inline-flex rounded-lg bg-secondary p-1"
            role="radiogroup"
            aria-label="Platform"
          >
            {PLATFORM_OPTIONS.map((option) => {
              const active = values.platform === option;
              return (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => set("platform", option)}
                  className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    active
                      ? "bg-card text-foreground shadow-[0_1px_2px_rgba(11,11,11,0.08)]"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {option === "both" ? "Both" : PLATFORM_LABELS[option]}
                </button>
              );
            })}
          </div>
        </fieldset>

        <div className="flex flex-col gap-2">
          <Label htmlFor="days" className="text-sm font-medium">
            Time window
          </Label>
          <Select
            value={String(values.days)}
            onValueChange={(value) => set("days", Number(value))}
          >
            <SelectTrigger id="days" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIME_WINDOWS.map((days) => (
                <SelectItem key={days} value={String(days)}>
                  Last {days} days
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

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
