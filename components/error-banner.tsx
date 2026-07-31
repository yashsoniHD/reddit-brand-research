"use client";

import { X } from "lucide-react";

import { Callout } from "@/components/callout";

/**
 * One dismissible banner above the form.
 *
 * The lifecycle rule this app keeps: any new user action clears whatever the
 * previous one said. A failure banner that outlives the thing it describes
 * reads as a live problem with whatever is on screen now.
 *
 * Built on the shared Callout for its tint, icon and metrics. Callout does
 * not include a dismiss control (only this banner needs one), so the button
 * and the messages[] mapping live here as Callout's children.
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
    <Callout tone="error" role="alert">
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {messages.map((message, index) => (
            <p key={`${index}-${message}`}>{message}</p>
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
    </Callout>
  );
}
