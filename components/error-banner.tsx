"use client";

import { AlertTriangle, X } from "lucide-react";

/**
 * One dismissible banner above the form.
 *
 * The lifecycle rule this app keeps: any new user action clears whatever the
 * previous one said. A failure banner that outlives the thing it describes
 * reads as a live problem with whatever is on screen now.
 */
export function ErrorBanner({
  messages,
  onDismiss,
}: {
  messages: string[];
  onDismiss: () => void;
}) {
  if (messages.length === 0) return null;

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-destructive/35 bg-destructive/8 px-4 py-3.5"
    >
      {/* Colour is never the only signal: the icon says "problem" on its own. */}
      <AlertTriangle
        aria-hidden="true"
        strokeWidth={2}
        className="mt-0.5 size-4 shrink-0 text-destructive"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {messages.map((message, index) => (
          <p
            key={`${index}-${message}`}
            className="text-sm leading-relaxed text-foreground"
          >
            {message}
          </p>
        ))}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="-my-1 -mr-1.5 flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-foreground"
      >
        <X aria-hidden="true" strokeWidth={2} className="size-4" />
        <span className="sr-only">Dismiss</span>
      </button>
    </div>
  );
}
